"""
資料完整度診斷（單一真相來源）

供「月度實帳金流分析」頁的診斷 UI 與「財報列印」的完整度把關共用。
compute_cashflow_health() 不依賴 streamlit，純算 issues / 表格資料。
"""

from __future__ import annotations

from collections import Counter

from data_processor.monthly_pl import _next_month, _prev_month


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

        n = _count("contract_expense", [
            ("eq", "clinic_id", fz_id), ("eq", "service_month", service_month),
        ])
        other_rows.append({"資料源": "x12 澤豐合約支出 (contract_expense)",
                           "月份": sm_label, "筆數": n,
                           "狀態": "✅" if n > 0 else "⚠️ 缺"})
        if n == 0:
            m = f"x12 contract_expense {sm_label} 缺資料：請上傳澤豐合約支出 xlsx"
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
