"""
月度損益分析（Phase 4b — 院長 2026-05-12 規格）

兩個診所的「會計精神」月度損益。與「月度實帳金流分析」(monthly_pl.py)
不同點：
- 月度實帳金流分析 = N 月實際發生的款項記在 N 月
- 月度損益分析     = N 月「歸屬」的款項記在 N 月（含跨月推算）

歸屬規則（依資料源各自有位移；見 calculate_clinic_pl 內各區段註解）：
  收入面：
    A 健保收入(點/%/值) ← nhi_payment_notices.service_month
                         （同月份多筆取 min(payment_date)）
    B 健保醫療給付      ← 玉山 inflow, summary='健保醫療給付',
                         memo_month 含 service_month 民國代碼
    C 現金總收入        ← N+1 月中信入帳（澤豐 x8 / 澤沛全 inflow）
    D 傳統整復推拿(澤豐)← 手KEY 補充備註 category='memo_only' entry_date=N月
    E 澤沛金流匯入(澤豐)← N+1 月澤豐中信 inflow note='豐沛金流'
    F 其餘收入          ← 澤豐: 手KEY 非常規收入(income, N月)
                         澤沛: 手KEY 非常規收入 + N+1月中信inflow排除E/股東注資

  支出面：
    H 薪資（細分3類）
      a 醫師薪資  - 周明毅: doctor_salary_monthly(N月)兩院總和(全算澤豐)
                  - 呂敏盛/胡舒婷: 對應玉山 outflow summary='薪資轉帳',
                    memo 含醫師姓名，attr_month=memo 內 YYYMM 或 入帳月-1
      b 護理師&助理 ← 玉山 outflow summary='薪資轉帳' 排除醫師，入帳月-1
      c 編制外人員  ← staff_salary_summary employee in external_names, N月
    I 現金支出
      澤豐 ← cash_expense.accrual_month=N月
      澤沛 ← N+1 月澤沛中信 outflow note 含「現金支出」/「現支」
    J 澤沛金流支出(澤沛) ← N+1 月澤沛中信 outflow note='豐沛金流'
    K 合約支出
      澤豐 ← contract_expense.service_month=N月
      澤沛 ← N+1 月澤沛中信 outflow note 含「合約」
    L 房租支出
      澤豐 ← 0 (含於合約)
      澤沛 ← N-1 月澤沛中信 outflow note 含「房租」（3月底匯 4月房租）
    M 其餘支出
      澤豐 ← 手KEY 非常規支出(N月) + N月玉山 outflow 排除院長個人/薪資
      澤沛 ← 手KEY 非常規支出(N月) + N+1月澤沛中信 outflow 排除上述4類
                                    + N月澤沛玉山 outflow 排除薪資
    P 支票支出 ← check_expense.issue_month=N月

  合計：
    G 總收入 = A_paid + B + C + D + E + F
              （A 是點數而非金額；總收入用 A 的「實付金額」即 B 已含，
               所以 G = B + C + D + E + F；A 只當資訊欄）
    N 總支出A = H + I + J + K + L + M
    O 盈餘A   = G - N
    Q 總支出B = H + I + J + P + L + M  （以支票替代合約）
    R 盈餘B   = G - Q
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date


# ─── 常數 / 預設名單 ──────────────────────────────────────
DEFAULT_DOCTOR_NAMES = ["周明毅", "呂敏盛", "胡舒婷"]
DEFAULT_EXTERNAL_NAMES = ["謝松坊"]
DEFAULT_ZHOU_ACCOUNTS = ["0668979072975", "137540125004"]

# 判斷玉山轉帳是否為「薪資」項：摘要列表
SALARY_SUMMARY_KEYWORDS = ("薪資轉帳", "薪資")


# ─── 日期 / 月份工具 ──────────────────────────────────────


def _month_offset(month: str, n: int) -> str:
    """ISO YYYY-MM-01 加減 n 個月。"""
    d = date.fromisoformat(month)
    new_y, new_m = d.year, d.month + n
    while new_m > 12:
        new_m -= 12
        new_y += 1
    while new_m < 1:
        new_m += 12
        new_y -= 1
    return date(new_y, new_m, 1).isoformat()


def _iso_to_roc_yyyymm(month: str) -> str:
    """2026-03-01 → '11503'"""
    d = date.fromisoformat(month)
    return f"{d.year - 1911}{d.month:02d}"


def _normalize(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", text or "")


def _digits_only(s: str) -> str:
    return "".join(c for c in (s or "") if c.isalnum())


def _extract_attr_month_from_memo(memo: str, fallback: str | None) -> str | None:
    """從備註字串抓 '11503' 等民國年月，轉 ISO；抓不到回 fallback。"""
    if not memo:
        return fallback
    s = _normalize(memo)
    m = re.search(r"1(\d{2})(\d{2})", s)
    if m:
        roc_y = int("1" + m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return f"{roc_y + 1911:04d}-{mo:02d}-01"
    return fallback


# ─── system_settings 讀取 ─────────────────────────────────


def _read_list(sb, key: str, default: list[str]) -> list[str]:
    try:
        rows = (
            sb.table("system_settings").select("value")
            .eq("key", key).limit(1).execute().data
        )
    except Exception:
        return default
    if not rows:
        return default
    v = rows[0].get("value")
    if v is None:
        return default
    if isinstance(v, list):
        return v
    try:
        parsed = json.loads(v)
        return parsed if isinstance(parsed, list) else default
    except (ValueError, TypeError):
        return default


# ─── Dataclass ─────────────────────────────────────────────


@dataclass
class ClinicMonthlyPL:
    """單診所、單月損益。"""
    service_month: str
    clinic_short: str

    # === A 健保收入(點/%/值) — 資訊欄，不入 G ===
    nhi_points: int = 0
    nhi_ratio_pct: float | None = None
    nhi_point_value: float | None = None
    nhi_paid_amount: int = 0  # 實付金額（HTML 上）— 供參照

    # === B 健保醫療給付 ===
    nhi_paid_items: list = field(default_factory=list)

    # === C 現金總收入 ===
    cash_revenue_items: list = field(default_factory=list)

    # === D 傳統整復推拿（澤豐 only；澤沛=0） ===
    massage_items: list = field(default_factory=list)

    # === E 澤沛金流匯入（澤豐 only；澤沛=0） ===
    zepei_inflow_items: list = field(default_factory=list)

    # === F 其餘收入 ===
    other_income_items: list = field(default_factory=list)

    # === H 薪資（細分） ===
    doctor_salary_items: list = field(default_factory=list)
    nurse_salary_items: list = field(default_factory=list)
    external_salary_items: list = field(default_factory=list)

    # === I 現金支出 ===
    cash_expense_items: list = field(default_factory=list)

    # === J 澤沛金流支出（澤沛 only；澤豐=0） ===
    zepei_outflow_items: list = field(default_factory=list)

    # === K 合約支出 ===
    contract_items: list = field(default_factory=list)

    # === L 房租支出（澤沛 only；澤豐=0 含於合約） ===
    rent_items: list = field(default_factory=list)

    # === M 其餘支出 ===
    other_expense_items: list = field(default_factory=list)

    # === P 支票支出 ===
    check_items: list = field(default_factory=list)

    # ── totals ──
    @staticmethod
    def _sum(items: list) -> int:
        return sum(int(it.get("amount") or 0) for it in items)

    @property
    def b_nhi_paid(self) -> int: return self._sum(self.nhi_paid_items)
    @property
    def c_cash_revenue(self) -> int: return self._sum(self.cash_revenue_items)
    @property
    def d_massage(self) -> int: return self._sum(self.massage_items)
    @property
    def e_zepei_inflow(self) -> int: return self._sum(self.zepei_inflow_items)
    @property
    def f_other_income(self) -> int: return self._sum(self.other_income_items)

    @property
    def h_doctor(self) -> int: return self._sum(self.doctor_salary_items)
    @property
    def h_nurse(self) -> int: return self._sum(self.nurse_salary_items)
    @property
    def h_external(self) -> int: return self._sum(self.external_salary_items)
    @property
    def h_salary_total(self) -> int:
        return self.h_doctor + self.h_nurse + self.h_external

    @property
    def i_cash_expense(self) -> int: return self._sum(self.cash_expense_items)
    @property
    def j_zepei_outflow(self) -> int: return self._sum(self.zepei_outflow_items)
    @property
    def k_contract(self) -> int: return self._sum(self.contract_items)
    @property
    def l_rent(self) -> int: return self._sum(self.rent_items)
    @property
    def m_other_expense(self) -> int: return self._sum(self.other_expense_items)
    @property
    def p_check(self) -> int: return self._sum(self.check_items)

    @property
    def g_total_income(self) -> int:
        """總收入 = B + C + D + E + F（A 是點數，不入合計）"""
        return (self.b_nhi_paid + self.c_cash_revenue + self.d_massage
                + self.e_zepei_inflow + self.f_other_income)

    @property
    def n_total_expense_a(self) -> int:
        return (self.h_salary_total + self.i_cash_expense + self.j_zepei_outflow
                + self.k_contract + self.l_rent + self.m_other_expense)

    @property
    def o_profit_a(self) -> int:
        return self.g_total_income - self.n_total_expense_a

    @property
    def q_total_expense_b(self) -> int:
        return (self.h_salary_total + self.i_cash_expense + self.j_zepei_outflow
                + self.p_check + self.l_rent + self.m_other_expense)

    @property
    def r_profit_b(self) -> int:
        return self.g_total_income - self.q_total_expense_b


# ─── Supabase helpers ──────────────────────────────────────


def _get_bank_account_id(sb, clinic_id: int, account_type: str) -> int | None:
    resp = (
        sb.table("bank_accounts").select("id")
        .eq("clinic_id", clinic_id).eq("account_type", account_type)
        .execute().data
    )
    return resp[0]["id"] if resp else None


def _fetch_bank_tx(sb, account_id: int, month_iso: str) -> list[dict]:
    next_m = _month_offset(month_iso, 1)
    return (
        sb.table("bank_transactions")
        .select(
            "transaction_date, summary, amount, counterparty, "
            "channel, note, memo_month"
        )
        .eq("account_id", account_id)
        .gte("transaction_date", month_iso)
        .lt("transaction_date", next_m)
        .order("transaction_date").order("id")
        .execute().data
    )


def _is_zhou_personal(counterparty: str, zhou_accounts: list[str]) -> bool:
    cp_digits = _digits_only(counterparty)
    for acc in zhou_accounts:
        if not acc:
            continue
        # 比對末段（去前置 0 後）
        acc_clean = acc.lstrip("0")
        if acc_clean and acc_clean in cp_digits:
            return True
    return False


def _has_doctor_name(text: str, doctor_names: list[str]) -> str | None:
    """memo 內含 doctor_names 任一姓名，回傳該姓名。"""
    if not text:
        return None
    for name in doctor_names:
        if name and name in text:
            return name
    return None


# ─── 收入面計算 ────────────────────────────────────────────


def _compute_revenue(
    sb, pl: ClinicMonthlyPL, clinic_id: int, zepei_clinic_id: int,
    esun_id: int | None, ctbc_id: int | None,
) -> None:
    sm = pl.service_month
    next_m = _month_offset(sm, 1)
    is_zefeng = (pl.clinic_short == "澤豐")
    target_roc = _iso_to_roc_yyyymm(sm)

    # === A 健保收入(點/%/值) — pick min(payment_date) per service_month ===
    rows = (
        sb.table("nhi_payment_notices")
        .select(
            "payment_date, applied_amount, interim_ratio_pct, point_value, "
            "paid_amount, payment_type"
        )
        .eq("clinic_id", clinic_id).eq("service_month", sm)
        .order("payment_date").execute().data
    )
    if rows:
        first = rows[0]
        pl.nhi_points = int(first.get("applied_amount") or 0)
        pl.nhi_ratio_pct = first.get("interim_ratio_pct")
        pl.nhi_point_value = first.get("point_value")
        pl.nhi_paid_amount = int(first.get("paid_amount") or 0)

    # === B 健保醫療給付 — 玉山 inflow 全期掃，memo_month 含 target_roc ===
    if esun_id:
        all_rows = (
            sb.table("bank_transactions")
            .select("transaction_date, summary, amount, memo_month")
            .eq("account_id", esun_id).gt("amount", 0)
            .execute().data
        )
        for r in all_rows:
            sm_str = (r.get("summary") or "").strip()
            if "健保醫療給付" not in sm_str and "健保署醫療給付" not in sm_str:
                continue
            memo = r.get("memo_month") or ""
            if target_roc in _normalize(memo):
                pl.nhi_paid_items.append({
                    "transaction_date": r.get("transaction_date"),
                    "summary": sm_str,
                    "memo": memo,
                    "amount": int(r.get("amount") or 0),
                })

    # === C 現金總收入 — N+1 月入帳 ===
    if is_zefeng:
        # 澤豐 = x8 中信現金入帳（需 manual_annotation 對應）
        if ctbc_id:
            ctbc_next = _fetch_bank_tx(sb, ctbc_id, next_m)
            ann = (
                sb.table("manual_annotation")
                .select("entry_date, amount, account, form, scope, "
                        "description, clinic_id, category")
                .eq("scope", "診所").in_("form", ["存現", "轉入"])
                .eq("account", "澤豐&個人中信")
                .gte("entry_date", next_m)
                .lt("entry_date", _month_offset(next_m, 1))
                .execute().data
            )
            ann = [r for r in ann
                   if (r.get("category") or "") != "memo_only"
                   and (r.get("clinic_id") in (clinic_id, None))]
            ann_keys = {(r["entry_date"], int(r["amount"] or 0)) for r in ann}
            ann_amt_only: dict[int, int] = {}
            for r in ann:
                ann_amt_only[int(r["amount"] or 0)] = (
                    ann_amt_only.get(int(r["amount"] or 0), 0) + 1
                )
            for tx in ctbc_next:
                amt = tx.get("amount") or 0
                if amt <= 0:
                    continue
                summary = tx.get("summary") or ""
                channel = tx.get("channel") or ""
                if "現金" not in summary and "存款機" not in channel:
                    continue
                tx_key = (tx.get("transaction_date"), int(amt))
                if tx_key not in ann_keys and ann_amt_only.get(int(amt), 0) != 1:
                    continue
                pl.cash_revenue_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "summary": summary,
                    "amount": int(amt),
                    "source": "澤豐中信現金",
                })
    else:
        # 澤沛 = 澤沛中信 inflow，排除 豐沛金流 + 股東注資
        if ctbc_id:
            ctbc_next = _fetch_bank_tx(sb, ctbc_id, next_m)
            # 股東注資 exclusion keys（澤沛中信）
            cap = (
                sb.table("manual_annotation")
                .select("entry_date, amount, account")
                .eq("category", "capital_injection")
                .eq("account", "澤沛中信")
                .gte("entry_date", next_m)
                .lt("entry_date", _month_offset(next_m, 1))
                .execute().data
            )
            cap_keys = {(r["entry_date"], int(r["amount"] or 0)) for r in cap}
            for tx in ctbc_next:
                amt = tx.get("amount") or 0
                if amt <= 0:
                    continue
                note_n = _normalize(tx.get("note") or "")
                if _is_fengpei(note_n):
                    continue  # 屬 E（澤沛是匯出方，inflow 不會有；保險排除）
                if (tx.get("transaction_date"), int(amt)) in cap_keys:
                    continue  # 股東注資排除
                # 澤沛 C 為「掛號費」現金；通常 summary 含「現金」或 channel='存款機'
                summary = tx.get("summary") or ""
                channel = tx.get("channel") or ""
                if "現金" in summary or "存款機" in channel:
                    pl.cash_revenue_items.append({
                        "transaction_date": tx.get("transaction_date"),
                        "summary": summary,
                        "amount": int(amt),
                        "source": "澤沛中信現金",
                    })

    # === D 傳統整復推拿（澤豐 only） ===
    if is_zefeng:
        memo_rows = (
            sb.table("manual_annotation")
            .select("entry_date, amount, description, clinic_id")
            .eq("category", "memo_only")
            .gte("entry_date", sm).lt("entry_date", next_m)
            .execute().data
        )
        for r in memo_rows:
            cid = r.get("clinic_id")
            if cid not in (clinic_id, None):
                continue
            pl.massage_items.append({
                "entry_date": r.get("entry_date"),
                "description": r.get("description") or "",
                "amount": int(r.get("amount") or 0),
            })

    # === E 澤沛金流匯入（澤豐 only；澤豐中信 N+1 月 inflow 豐沛金流） ===
    if is_zefeng and ctbc_id:
        for tx in _fetch_bank_tx(sb, ctbc_id, next_m):
            amt = tx.get("amount") or 0
            if amt <= 0:
                continue
            if _is_fengpei(_normalize(tx.get("note") or "")):
                pl.zepei_inflow_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "note": tx.get("note") or "",
                    "amount": int(amt),
                })

    # === F 其餘收入 ===
    # 手KEY 非常規收入（兩家都有）
    me_rows = (
        sb.table("manual_entry")
        .select("entry_date, description, amount")
        .eq("clinic_id", clinic_id).eq("direction", "income")
        .gte("entry_date", sm).lt("entry_date", next_m)
        .execute().data
    )
    for r in me_rows:
        pl.other_income_items.append({
            "entry_date": r.get("entry_date"),
            "description": r.get("description") or "",
            "amount": int(r.get("amount") or 0),
            "source": "手KEY 非常規收入",
        })
    # 澤沛還要加：N+1 月中信 inflow 排除豐沛/股東/現金(已歸C)
    if not is_zefeng and ctbc_id:
        ctbc_next = _fetch_bank_tx(sb, ctbc_id, next_m)
        cap = (
            sb.table("manual_annotation")
            .select("entry_date, amount, account")
            .eq("category", "capital_injection")
            .eq("account", "澤沛中信")
            .gte("entry_date", next_m)
            .lt("entry_date", _month_offset(next_m, 1))
            .execute().data
        )
        cap_keys = {(r["entry_date"], int(r["amount"] or 0)) for r in cap}
        for tx in ctbc_next:
            amt = tx.get("amount") or 0
            if amt <= 0:
                continue
            note_n = _normalize(tx.get("note") or "")
            summary = tx.get("summary") or ""
            channel = tx.get("channel") or ""
            if _is_fengpei(note_n):
                continue
            if "現金" in summary or "存款機" in channel:
                continue  # 已歸 C
            if (tx.get("transaction_date"), int(amt)) in cap_keys:
                continue  # 股東注資
            pl.other_income_items.append({
                "transaction_date": tx.get("transaction_date"),
                "summary": summary,
                "note": tx.get("note") or "",
                "amount": int(amt),
                "source": "澤沛中信其他入帳",
            })


def _is_fengpei(note_norm: str) -> bool:
    return any(k in note_norm for k in (
        "豐沛金流", "沛豐金流", "澤沛金流", "沛to豐", "沛 to 豐",
    ))


def _is_zepei_cash_outflow(note_norm: str) -> bool:
    return "現金支出" in note_norm or "現支" in note_norm


def _is_zepei_contract(note_norm: str) -> bool:
    return "合約" in note_norm


def _is_zepei_rent(note_norm: str) -> bool:
    return "房租" in note_norm


# ─── 支出面計算 ────────────────────────────────────────────


def _compute_expense(
    sb, pl: ClinicMonthlyPL, clinic_id: int, zepei_clinic_id: int,
    esun_id: int | None, ctbc_id: int | None,
    doctor_names: list[str], external_names: list[str],
    zhou_accounts: list[str],
) -> None:
    sm = pl.service_month
    next_m = _month_offset(sm, 1)
    prev_m = _month_offset(sm, -1)
    is_zefeng = (pl.clinic_short == "澤豐")

    # === H 薪資 ===
    # H a 周明毅：doctor_salary_monthly N月 total_salary（兩院總和，全算澤豐）
    if is_zefeng:
        zhou = (
            sb.table("doctors").select("id").eq("name", "周明毅").execute().data
        )
        if zhou:
            zsal = (
                sb.table("doctor_salary_monthly")
                .select("total_salary, service_month, clinic_id")
                .eq("doctor_id", zhou[0]["id"]).eq("service_month", sm)
                .execute().data
            )
            for r in zsal:
                pl.doctor_salary_items.append({
                    "doctor": "周明毅",
                    "service_month": r.get("service_month"),
                    "amount": int(r.get("total_salary") or 0),
                    "source": "doctor_salary_monthly",
                })

    # H a 其他醫師 + H b 護理師&助理：
    # 看 N+1 月玉山 outflow，summary='薪資轉帳' 或 含「薪資」
    if esun_id:
        for tx in _fetch_bank_tx(sb, esun_id, next_m):
            amt = tx.get("amount") or 0
            if amt >= 0:
                continue
            summary = tx.get("summary") or ""
            cp = tx.get("counterparty") or ""
            memo = tx.get("memo_month") or ""
            # 院長個人轉帳排除
            if _is_zhou_personal(cp, zhou_accounts):
                continue
            # 必須是薪資項
            if not any(k in summary for k in SALARY_SUMMARY_KEYWORDS):
                continue
            attr = _extract_attr_month_from_memo(memo, fallback=sm)
            if attr != sm:
                continue
            # 是否含醫師姓名（排除周明毅，已從 doctor_salary_monthly 算）
            doctor_hit = _has_doctor_name(memo, doctor_names)
            amount_abs = -int(amt)
            if doctor_hit and doctor_hit != "周明毅":
                pl.doctor_salary_items.append({
                    "doctor": doctor_hit,
                    "transaction_date": tx.get("transaction_date"),
                    "memo": memo,
                    "amount": amount_abs,
                    "source": "玉山轉帳",
                })
            elif not doctor_hit:
                pl.nurse_salary_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "memo": memo,
                    "counterparty": cp,
                    "amount": amount_abs,
                    "source": "玉山轉帳",
                })
            # doctor_hit == 周明毅 → 跳過(走 doctor_salary_monthly)

    # H c 編制外人員 (謝松坊 etc)：staff_salary_summary service_month=N月
    if external_names:
        ss = (
            sb.table("staff_salary_summary")
            .select("employee_label, gross_salary, service_month, "
                    "paid_by_clinic_id")
            .eq("clinic_id", clinic_id).eq("service_month", sm)
            .execute().data
        )
        for r in ss:
            label = r.get("employee_label") or ""
            if any(n in label for n in external_names):
                pl.external_salary_items.append({
                    "employee_label": label,
                    "service_month": r.get("service_month"),
                    "amount": int(r.get("gross_salary") or 0),
                    "source": "staff_salary_summary",
                })

    # === I 現金支出 ===
    if is_zefeng:
        # 澤豐：cash_expense.accrual_month=N月
        rows = (
            sb.table("cash_expense")
            .select("expense_date, description, amount, accrual_month")
            .eq("clinic_id", clinic_id).eq("accrual_month", sm)
            .execute().data
        )
        for r in rows:
            pl.cash_expense_items.append({
                "expense_date": r.get("expense_date"),
                "description": r.get("description") or "",
                "amount": int(r.get("amount") or 0),
                "source": "cash_expense",
            })
    else:
        # 澤沛：N+1 月澤沛中信 outflow note 含「現金支出/現支」
        if ctbc_id:
            for tx in _fetch_bank_tx(sb, ctbc_id, next_m):
                amt = tx.get("amount") or 0
                if amt >= 0:
                    continue
                if _is_zepei_cash_outflow(_normalize(tx.get("note") or "")):
                    pl.cash_expense_items.append({
                        "transaction_date": tx.get("transaction_date"),
                        "note": tx.get("note") or "",
                        "amount": -int(amt),
                        "source": "澤沛中信現金支出",
                    })

    # === J 澤沛金流支出（澤沛 only） ===
    if not is_zefeng and ctbc_id:
        for tx in _fetch_bank_tx(sb, ctbc_id, next_m):
            amt = tx.get("amount") or 0
            if amt >= 0:
                continue
            if _is_fengpei(_normalize(tx.get("note") or "")):
                pl.zepei_outflow_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "note": tx.get("note") or "",
                    "amount": -int(amt),
                })

    # === K 合約支出 ===
    if is_zefeng:
        rows = (
            sb.table("contract_expense")
            .select("service_month, vendor, amount, note")
            .eq("clinic_id", clinic_id).eq("service_month", sm)
            .execute().data
        )
        for r in rows:
            pl.contract_items.append({
                "service_month": r.get("service_month"),
                "vendor": r.get("vendor") or "",
                "amount": int(round(float(r.get("amount") or 0))),
                "source": "contract_expense",
            })
    else:
        if ctbc_id:
            for tx in _fetch_bank_tx(sb, ctbc_id, next_m):
                amt = tx.get("amount") or 0
                if amt >= 0:
                    continue
                if _is_zepei_contract(_normalize(tx.get("note") or "")):
                    pl.contract_items.append({
                        "transaction_date": tx.get("transaction_date"),
                        "note": tx.get("note") or "",
                        "amount": -int(amt),
                        "source": "澤沛中信合約",
                    })

    # === L 房租支出（澤沛 only） ===
    if not is_zefeng and ctbc_id:
        # 3月底匯 4月房租 → 看 N-1 月澤沛中信 outflow
        for tx in _fetch_bank_tx(sb, ctbc_id, prev_m):
            amt = tx.get("amount") or 0
            if amt >= 0:
                continue
            if _is_zepei_rent(_normalize(tx.get("note") or "")):
                pl.rent_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "note": tx.get("note") or "",
                    "amount": -int(amt),
                    "source": "澤沛中信房租",
                })

    # === M 其餘支出 ===
    # 手KEY 非常規支出（兩家都有）
    me_ex = (
        sb.table("manual_entry")
        .select("entry_date, description, amount")
        .eq("clinic_id", clinic_id).eq("direction", "expense")
        .gte("entry_date", sm).lt("entry_date", next_m)
        .execute().data
    )
    for r in me_ex:
        pl.other_expense_items.append({
            "entry_date": r.get("entry_date"),
            "description": r.get("description") or "",
            "amount": int(r.get("amount") or 0),
            "source": "手KEY 非常規支出",
        })

    if is_zefeng:
        # 澤豐：N 月玉山 outflow 排除（院長個人 + 薪資轉帳）
        if esun_id:
            for tx in _fetch_bank_tx(sb, esun_id, sm):
                amt = tx.get("amount") or 0
                if amt >= 0:
                    continue
                summary = tx.get("summary") or ""
                cp = tx.get("counterparty") or ""
                if _is_zhou_personal(cp, zhou_accounts):
                    continue
                if any(k in summary for k in SALARY_SUMMARY_KEYWORDS):
                    continue
                pl.other_expense_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "summary": summary,
                    "counterparty": cp,
                    "memo": tx.get("memo_month") or "",
                    "amount": -int(amt),
                    "source": "澤豐玉山其他支出",
                })
    else:
        # 澤沛：N+1 月中信 outflow 排除（現支/豐沛/合約/房租）
        if ctbc_id:
            for tx in _fetch_bank_tx(sb, ctbc_id, next_m):
                amt = tx.get("amount") or 0
                if amt >= 0:
                    continue
                note_n = _normalize(tx.get("note") or "")
                if (_is_fengpei(note_n) or _is_zepei_cash_outflow(note_n)
                        or _is_zepei_contract(note_n)
                        or _is_zepei_rent(note_n)):
                    continue
                pl.other_expense_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "summary": tx.get("summary") or "",
                    "note": tx.get("note") or "",
                    "amount": -int(amt),
                    "source": "澤沛中信其他支出",
                })
        # 澤沛玉山 outflow 排除薪資（已歸 H）
        if esun_id:
            for tx in _fetch_bank_tx(sb, esun_id, sm):
                amt = tx.get("amount") or 0
                if amt >= 0:
                    continue
                summary = tx.get("summary") or ""
                if any(k in summary for k in SALARY_SUMMARY_KEYWORDS):
                    continue
                pl.other_expense_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "summary": summary,
                    "memo": tx.get("memo_month") or "",
                    "amount": -int(amt),
                    "source": "澤沛玉山其他支出",
                })

    # === P 支票支出 ===
    rows = (
        sb.table("check_expense")
        .select("issue_month, vendor, amount, bank, note")
        .eq("issue_month", sm).execute().data
    )
    # check_expense 沒有 clinic_id；目前 spec 算兩家共用，這裡都算進去
    # 之後若要區分再加邏輯
    if is_zefeng:
        for r in rows:
            pl.check_items.append({
                "vendor": r.get("vendor") or "",
                "amount": int(r.get("amount") or 0),
                "source": "check_expense",
            })


# ─── 高層 API ──────────────────────────────────────────────


def calculate_clinic_pl(
    sb, service_month: str, clinic_id: int, clinic_short: str,
    zepei_clinic_id: int,
    doctor_names: list[str], external_names: list[str],
    zhou_accounts: list[str],
) -> ClinicMonthlyPL:
    pl = ClinicMonthlyPL(
        service_month=service_month, clinic_short=clinic_short,
    )
    esun_id = _get_bank_account_id(sb, clinic_id, "健保戶")
    ctbc_id = _get_bank_account_id(sb, clinic_id, "進出戶")
    _compute_revenue(sb, pl, clinic_id, zepei_clinic_id, esun_id, ctbc_id)
    _compute_expense(
        sb, pl, clinic_id, zepei_clinic_id, esun_id, ctbc_id,
        doctor_names, external_names, zhou_accounts,
    )
    return pl


def calculate_both_pl(
    sb, service_month: str,
) -> tuple[ClinicMonthlyPL, ClinicMonthlyPL]:
    """一次算澤豐 + 澤沛月度損益。"""
    clinics = sb.table("clinics").select("id, short_name").execute().data
    fz = next(c for c in clinics if c["short_name"] == "澤豐")
    fp = next(c for c in clinics if c["short_name"] == "澤沛")
    doctor_names = _read_list(sb, "doctor_names", DEFAULT_DOCTOR_NAMES)
    external_names = _read_list(
        sb, "external_staff_names", DEFAULT_EXTERNAL_NAMES,
    )
    zhou_accounts = _read_list(
        sb, "zhou_personal_accounts", DEFAULT_ZHOU_ACCOUNTS,
    )
    pl_fz = calculate_clinic_pl(
        sb, service_month, fz["id"], "澤豐", fp["id"],
        doctor_names, external_names, zhou_accounts,
    )
    pl_fp = calculate_clinic_pl(
        sb, service_month, fp["id"], "澤沛", fp["id"],
        doctor_names, external_names, zhou_accounts,
    )
    return pl_fz, pl_fp


def list_available_months(sb) -> list[str]:
    """聚合多個來源的可用月份。"""
    months: set[str] = set()
    try:
        for r in (sb.table("bank_transactions")
                    .select("transaction_date").execute().data or []):
            d = r.get("transaction_date")
            if d:
                months.add(d[:7] + "-01")
    except Exception:
        pass
    try:
        for r in (sb.table("nhi_payment_notices")
                    .select("service_month").execute().data or []):
            sm = r.get("service_month")
            if sm:
                months.add(sm)
    except Exception:
        pass
    return sorted(months, reverse=True)
