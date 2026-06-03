"""
資料完整度診斷（單一真相來源）

供「月度實帳金流分析」頁的診斷 UI 與「財報列印」的完整度把關共用。
compute_cashflow_health() 不依賴 streamlit，純算 issues / 表格資料。
"""

from __future__ import annotations

import unicodedata
from collections import Counter

from data_processor.monthly_pl import _next_month, _prev_month

# 澤豐合約支出「每月必填」的 10 個廠商（對應合約支出檔 R0 表頭）。
# 規則：該月每個廠商都要有資料列（填 0 算已填，留空才算缺）。
REQUIRED_CONTRACT_VENDORS = [
    "莊松榮簽口", "港香蘭簽口", "天一簽口", "科達簽口", "順天堂無票合約",
    "大墩叫貨", "駿賀簽口", "力至高簽口", "德瑞適&神美針", "房租(玉)",
]


def _norm_vendor(s) -> str:
    """NFKC 正規化 + 去空白；吸收全形/半形括號差異。"""
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def compute_pl_health(sb, service_month: str) -> dict:
    """
    月度損益(會計精神)的資料完整度。
    因損益需大量「下個月(N+1)」銀行明細回推，故完整門檻為：
      - N+1 月 玉山健保戶 CSV 已上傳（該診所）
      - N+1 月 中信進出戶 CSV 已上傳（該診所）
      - N 月 健保給付(nhi_payment_notices)至少 1 筆（健保第一筆給付，該診所）
    任一缺 → 該診所該月視為不完整。

    Returns dict: issues_fz / issues_fp / complete_fz / complete_fp /
                  rows(list[dict] 診斷表)
    """
    next_m = _next_month(service_month)
    nn_m = _next_month(next_m)
    sm_label = service_month[:7]
    nx_label = next_m[:7]

    clinics = sb.table("clinics").select("id, short_name").execute().data
    fz_id = next((c["id"] for c in clinics if c["short_name"] == "澤豐"), None)
    fp_id = next((c["id"] for c in clinics if c["short_name"] == "澤沛"), None)
    accounts = (
        sb.table("bank_accounts")
        .select("id, clinic_id, account_type").execute().data
    )

    def _acc(clinic_id, atype):
        return next((a["id"] for a in accounts
                     if a["clinic_id"] == clinic_id
                     and a["account_type"] == atype), None)

    def _bank_n(acc_id, m0, m1) -> int:
        if not acc_id:
            return 0
        return (sb.table("bank_transactions").select("id", count="exact")
                .eq("account_id", acc_id)
                .gte("transaction_date", m0).lt("transaction_date", m1)
                .execute().count or 0)

    def _nhi_n(clinic_id) -> int:
        return (sb.table("nhi_payment_notices").select("id", count="exact")
                .eq("clinic_id", clinic_id).eq("service_month", service_month)
                .execute().count or 0)

    def _clinic_issues(clinic_id, short) -> list[str]:
        out = []
        if _bank_n(_acc(clinic_id, "健保戶"), next_m, nn_m) == 0:
            out.append(f"{short} 玉山健保戶 {nx_label} CSV 未上傳（損益回推需要）")
        if _bank_n(_acc(clinic_id, "進出戶"), next_m, nn_m) == 0:
            out.append(f"{short} 中信進出戶 {nx_label} CSV 未上傳（損益回推需要）")
        if _nhi_n(clinic_id) == 0:
            out.append(f"{short} 健保給付 {sm_label} 尚無第一筆給付資料")
        return out

    issues_fz = _clinic_issues(fz_id, "澤豐") if fz_id else ["找不到澤豐"]
    issues_fp = _clinic_issues(fp_id, "澤沛") if fp_id else ["找不到澤沛"]

    rows = [{
        "月份": sm_label,
        "澤豐": "✅ 完整" if not issues_fz else f"⚠️ 缺 {len(issues_fz)} 項",
        "澤沛": "✅ 完整" if not issues_fp else f"⚠️ 缺 {len(issues_fp)} 項",
    }]
    return {
        "issues_fz": issues_fz,
        "issues_fp": issues_fp,
        "complete_fz": not issues_fz,
        "complete_fp": not issues_fp,
        "rows": rows,
    }


def compute_cashflow_health(sb, service_month: str) -> dict:
    """
    回傳該月實帳金流的資料完整度。

    Returns dict:
      issues:      list[str]  全部缺漏清單
      issues_fz:   list[str]  澤豐相關缺漏
      issues_fp:   list[str]  澤沛相關缺漏
      bank_table:  list[dict] 銀行交易筆數表（UI 用）
      other_rows:  list[dict] 其他資料源筆數表（UI 用）
    """
    next_m = _next_month(service_month)
    prev_m = _prev_month(service_month)
    sm_label = service_month[:7]
    pm_label = prev_m[:7]

    accounts = (
        sb.table("bank_accounts")
        .select("id, clinic_id, bank, account_type, is_personal_mixed")
        .execute().data
    )
    clinic_resp = sb.table("clinics").select("id, short_name").execute().data
    cid_to_short = {c["id"]: c["short_name"] for c in clinic_resp}
    fz_id = next((c["id"] for c in clinic_resp if c["short_name"] == "澤豐"), None)
    fp_id = next((c["id"] for c in clinic_resp if c["short_name"] == "澤沛"), None)

    tx_rows = (
        sb.table("bank_transactions").select("account_id")
        .gte("transaction_date", service_month).lt("transaction_date", next_m)
        .execute().data
    )
    tx_counts = Counter(r["account_id"] for r in tx_rows)

    bank_table: list[dict] = []
    issues: list[str] = []
    issues_fz: list[str] = []
    issues_fp: list[str] = []

    for acc in accounts:
        clinic = cid_to_short.get(acc["clinic_id"], "?")
        bank = acc.get("bank", "?")
        atype = acc.get("account_type", "?")
        if acc.get("is_personal_mixed"):
            atype = f"{atype}（混戶）"
        n = tx_counts.get(acc["id"], 0)
        bank_table.append({
            "診所": clinic,
            "戶別": f"{bank} {atype}",
            f"{sm_label} 筆數": n,
            "狀態": "✅" if n > 0 else "⚠️ 缺",
        })
        if n == 0:
            msg = f"{clinic} {bank} {atype}：{sm_label} CSV 未上傳"
            issues.append(msg)
            if acc["clinic_id"] == fz_id:
                issues_fz.append(msg)
            elif acc["clinic_id"] == fp_id:
                issues_fp.append(msg)

    def _count(table: str, filters: list[tuple[str, str, object]]) -> int:
        q = sb.table(table).select("id", count="exact")
        for op, col, val in filters:
            q = getattr(q, op)(col, val)
        return q.execute().count or 0

    other_rows: list[dict] = []
    if fz_id:
        n = _count("cash_expense", [
            ("eq", "clinic_id", fz_id), ("eq", "accrual_month", service_month),
        ])
        other_rows.append({"資料源": "x3 澤豐現金支出 (cash_expense)",
                           "月份": sm_label, "筆數": n,
                           "狀態": "✅" if n > 0 else "⚠️ 缺"})
        if n == 0:
            m = f"x3 cash_expense {sm_label} 缺資料：請上傳澤豐現金支出 xlsx"
            issues.append(m); issues_fz.append(m)

        crows = (
            sb.table("contract_expense").select("vendor")
            .eq("clinic_id", fz_id).eq("service_month", service_month)
            .execute().data
        )
        present = {_norm_vendor(r.get("vendor")) for r in crows}
        missing = [v for v in REQUIRED_CONTRACT_VENDORS
                   if _norm_vendor(v) not in present]
        n_req = len(REQUIRED_CONTRACT_VENDORS)
        other_rows.append({
            "資料源": "x12 澤豐合約支出 (contract_expense)",
            "月份": sm_label,
            "筆數": f"{n_req - len(missing)}/{n_req} 廠商",
            "狀態": "✅" if not missing else "⚠️ 缺",
        })
        if missing:
            if not crows:
                m = (f"x12 contract_expense {sm_label} 缺資料："
                     "請上傳澤豐合約支出 xlsx")
            else:
                m = (f"x12 澤豐合約支出 {sm_label} 有 {len(missing)} 廠商留空："
                     f"{'、'.join(missing)}（請在合約支出檔填值，可填 0 但勿留空後重傳）")
            issues.append(m); issues_fz.append(m)

        rows_x9 = (
            sb.table("staff_salary_summary")
            .select("id, employee_label")
            .eq("clinic_id", fz_id).eq("service_month", prev_m)
            .execute().data
        )
        n_x9 = sum(1 for r in rows_x9 if "謝松坊" in (r.get("employee_label") or ""))
        other_rows.append({"資料源": f"x9 謝松坊薪資 ({pm_label} 服務月)",
                           "月份": pm_label, "筆數": n_x9,
                           "狀態": "✅" if n_x9 > 0 else "⚠️ 缺"})
        if n_x9 == 0:
            m = (f"x9 staff_salary_summary {pm_label} 沒謝松坊："
                 "請到員工薪資頁按「全部月份一次匯入」")
            issues.append(m); issues_fz.append(m)

        zhou = sb.table("doctors").select("id").eq("name", "周明毅").execute().data
        if zhou:
            zhou_id = zhou[0]["id"]
            n_x13 = _count("doctor_salary_monthly", [
                ("eq", "doctor_id", zhou_id), ("eq", "service_month", prev_m),
            ])
            other_rows.append({"資料源": f"x13 周院長薪資 ({pm_label} 服務月)",
                               "月份": pm_label, "筆數": n_x13,
                               "狀態": "✅" if n_x13 > 0 else "⚠️ 缺"})
            if n_x13 == 0:
                m = (f"x13 doctor_salary_monthly {pm_label} 沒周院長："
                     f"請到醫師薪資頁選 {pm_label} 並按「💾 寫入」")
                issues.append(m); issues_fz.append(m)

        ann_rows = (
            sb.table("manual_annotation").select("id, description")
            .eq("scope", "診所").eq("form", "存現").eq("account", "澤豐&個人中信")
            .eq("clinic_id", fz_id)
            .gte("entry_date", service_month).lt("entry_date", next_m)
            .execute().data
        )
        other_rows.append({"資料源": "x8 manual_annotation（澤豐存現標記）",
                           "月份": sm_label, "筆數": len(ann_rows),
                           "狀態": "✅" if len(ann_rows) > 0 else "ℹ️ 無"})
        # x8 缺註記不一定是問題（該月沒存現），不放進 issues

    return {
        "issues": issues,
        "issues_fz": issues_fz,
        "issues_fp": issues_fp,
        "bank_table": bank_table,
        "other_rows": other_rows,
    }
