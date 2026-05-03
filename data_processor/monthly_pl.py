"""
月度損益（實帳模式 v2）— 院長 2026-05-04 補充規則

核心原則：每筆款項按「實際入帳/出帳的銀行交易日期」歸屬月份，
不用「服務月（業績）」歸屬。

健保以「玉山健保戶 bank_transactions」為基準，
nhi_payment_notices 只用於核對 + 取 A/B/C/點值（業績用）。

兩家邏輯：
  • 澤沛（簡單）：玉山健保戶 + 中信進出戶逐筆按 transaction_date 月份聚合
  • 澤豐（複雜）：12 變數規則
    x1  前月餘額                 中信月初餘額
    x2  玉山健保轉入             中信進出戶 counterparty=玉山的入帳
    x3  澤豐現金支出（隱形）     cash_expense clinic=澤豐 當月 accrual
    x4  澤沛現金支出代墊（隱形） cash_expense clinic=澤沛 當月 accrual
    x5  前月澤沛現金結算還款      （4月入帳 = 3月收入）  ← 跨月
    x6  澤沛→澤豐金流            （4月入帳 = 3月收入）  ← 跨月
    x7  澤沛合約進帳              （4月入帳 = 3月收入）  ← 跨月
    x8  澤豐現金入帳補存         中信存款機 現金存入  ← 跨月（4月存=3月收）
    x9  編制外人力薪資（謝松坊） staff_salary 中該員工
    x10 手 KEY 非常規收支         manual_entry
    x11 當月餘額                 中信月末餘額
    x12 澤豐合約支出             contract_expense clinic=澤豐 當月

健保收入 (從玉山健保戶 bank_transactions)：summary 含「健保醫療給付」
員工薪資扣款（玉山健保戶）：summary 含「薪資轉帳」「健保扣繳」「勞保」
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class ZepeiMonthly:
    """澤沛實帳（簡單版）"""
    service_month: str
    # 收入
    nhi_inflow: int = 0          # 玉山健保戶健保入帳
    cross_inflow: int = 0        # 中信跨診所匯入
    cash_deposit: int = 0        # 中信現金存入
    other_income: int = 0
    misc_income: int = 0         # 手 KEY income
    # 支出
    salary_outflow: int = 0      # 玉山薪資轉帳出
    nhi_premium: int = 0         # 玉山健保代繳
    contract_outflow: int = 0    # 中信轉廠商
    cross_outflow: int = 0       # 中信跨診所匯出
    other_expense: int = 0
    cash_expense_total: int = 0  # cash_expense 當月（澤沛 self）
    contract_expense_total: int = 0  # contract_expense 當月
    misc_expense: int = 0
    staff_salary: int = 0        # staff_salary_summary 當月
    doctor_salary: int = 0       # doctor_salary_monthly 當月

    @property
    def total_income(self) -> int:
        return (
            self.nhi_inflow + self.cross_inflow + self.cash_deposit
            + self.other_income + self.misc_income
        )

    @property
    def total_expense(self) -> int:
        # 銀行扣款（薪資/健保/合約/跨診所/其他）已是現金流出
        # cash_expense / contract_expense 是「實際支出記帳」歸屬該月（避免重複）
        # 這裡只用代表月度支出的 cash_expense_total / contract_expense_total / staff/doctor salary
        return (
            self.cash_expense_total + self.contract_expense_total
            + self.staff_salary + self.doctor_salary + self.misc_expense
        )

    @property
    def net(self) -> int:
        return self.total_income - self.total_expense


@dataclass
class ZefengMonthly:
    """澤豐實帳（12 變數）"""
    service_month: str
    # 玉山健保戶（實際銀行）
    nhi_inflow: int = 0          # 健保醫療給付
    nhi_premium_outflow: int = 0  # 健保代繳
    salary_outflow_esun: int = 0  # 玉山薪資轉帳
    other_esun_in: int = 0
    other_esun_out: int = 0
    # 中信進出戶（11 變數）
    x1_prev_balance: int = 0
    x2_zefeng_inflow: int = 0
    x5_zepei_prev_repay: int = 0
    x6_fengpei_settle: int = 0
    x7_zepei_contract_repay: int = 0
    x8_zefeng_cash_revenue: int = 0
    x10_misc: int = 0  # net (income - expense)
    x11_current_balance: int = 0
    # 隱形支出（已知為當月支出但中信看不到逐筆）
    x3_zefeng_cash_expense: int = 0
    x4_zepei_cash_expense_proxy: int = 0  # 代墊（澤沛之後還）
    x9_offsite_staff_pay: int = 0
    x12_zefeng_contract_expense: int = 0
    # 系統計算
    doctor_salary: int = 0
    staff_salary: int = 0  # 該診所主聘員工
    misc_income_x10: int = 0
    misc_expense_x10: int = 0

    @property
    def total_income(self) -> int:
        """總收入 = 健保入帳 + 玉山其他收入 + x5 + x6 + x7 + x8 + x10收入 + x2"""
        return (
            self.nhi_inflow + self.other_esun_in
            + self.x2_zefeng_inflow
            + self.x5_zepei_prev_repay
            + self.x6_fengpei_settle
            + self.x7_zepei_contract_repay
            + self.x8_zefeng_cash_revenue
            + self.misc_income_x10
        )

    @property
    def total_expense(self) -> int:
        """總支出 = 薪資 + 醫師薪資 + x3 + x4 + x9 + x12 + 健保代繳 + x10支出"""
        return (
            self.staff_salary + self.doctor_salary
            + self.x3_zefeng_cash_expense
            + self.x4_zepei_cash_expense_proxy
            + self.x9_offsite_staff_pay
            + self.x12_zefeng_contract_expense
            + self.nhi_premium_outflow
            + self.misc_expense_x10
        )

    @property
    def net(self) -> int:
        return self.total_income - self.total_expense


# ============================================================================
# Helpers
# ============================================================================


def _next_month(service_month: str) -> str:
    d = date.fromisoformat(service_month)
    if d.month == 12:
        n = date(d.year + 1, 1, 1)
    else:
        n = date(d.year, d.month + 1, 1)
    return n.isoformat()


def _prev_month(service_month: str) -> str:
    d = date.fromisoformat(service_month)
    if d.month == 1:
        n = date(d.year - 1, 12, 1)
    else:
        n = date(d.year, d.month - 1, 1)
    return n.isoformat()


def _sum_amount(rows: list[dict], field_name: str = "amount") -> int:
    return sum((r.get(field_name) or 0) for r in rows)


# ============================================================================
# Sheet 抓取
# ============================================================================


def _get_bank_account_id(sb, clinic_id: int, account_type: str) -> int | None:
    resp = (
        sb.table("bank_accounts")
        .select("id")
        .eq("clinic_id", clinic_id)
        .eq("account_type", account_type)
        .execute().data
    )
    if resp:
        return resp[0]["id"]
    return None


def _fetch_bank_transactions(
    sb, account_id: int, service_month: str
) -> list[dict]:
    next_month = _next_month(service_month)
    return (
        sb.table("bank_transactions")
        .select("transaction_date, summary, amount, counterparty, channel, note, memo_month")
        .eq("account_id", account_id)
        .gte("transaction_date", service_month)
        .lt("transaction_date", next_month)
        .execute().data
    )


# ============================================================================
# 澤沛 — 簡單聚合
# ============================================================================


def calculate_zepei_monthly(sb, service_month: str, clinic_id: int) -> ZepeiMonthly:
    m = ZepeiMonthly(service_month=service_month)
    next_month = _next_month(service_month)

    # 玉山健保戶
    esun_id = _get_bank_account_id(sb, clinic_id, "健保戶")
    if esun_id:
        for tx in _fetch_bank_transactions(sb, esun_id, service_month):
            amt = tx["amount"]
            summary = (tx.get("summary") or "")
            if amt > 0:
                if "健保醫療給付" in summary or "健保" in summary:
                    m.nhi_inflow += amt
                else:
                    m.other_income += amt
            else:
                a = -amt
                if "薪資轉帳" in summary or "薪資" in summary:
                    m.salary_outflow += a
                elif "健保" in summary or "勞保" in summary:
                    m.nhi_premium += a
                else:
                    m.other_expense += a

    # 中信進出戶
    ctbc_id = _get_bank_account_id(sb, clinic_id, "進出戶")
    if ctbc_id:
        for tx in _fetch_bank_transactions(sb, ctbc_id, service_month):
            amt = tx["amount"]
            summary = (tx.get("summary") or "")
            cp = (tx.get("counterparty") or "")
            note = (tx.get("note") or "")
            if amt > 0:
                if "澤豐" in note or "澤豐" in cp:
                    m.cross_inflow += amt
                elif "現金" in summary:
                    m.cash_deposit += amt
                else:
                    m.other_income += amt
            else:
                a = -amt
                if "澤豐" in note or "澤豐" in cp:
                    m.cross_outflow += a
                elif any(k in note for k in ("莊松榮", "港香蘭", "天一", "駿賀", "大墩", "順天")):
                    m.contract_outflow += a
                else:
                    m.other_expense += a

    # 系統計算的薪資/支出彙總
    cash = (
        sb.table("cash_expense").select("amount")
        .eq("clinic_id", clinic_id).eq("accrual_month", service_month)
        .execute().data
    )
    m.cash_expense_total = _sum_amount(cash)

    contract = (
        sb.table("contract_expense").select("amount")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    m.contract_expense_total = int(_sum_amount(contract))

    ss = (
        sb.table("staff_salary_summary").select("gross_salary")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    m.staff_salary = _sum_amount(ss, "gross_salary")

    ds = (
        sb.table("doctor_salary_monthly").select("total_salary")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    m.doctor_salary = _sum_amount(ds, "total_salary")

    me_in = (
        sb.table("manual_entry").select("amount")
        .eq("clinic_id", clinic_id).eq("direction", "income")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    m.misc_income = _sum_amount(me_in)

    me_ex = (
        sb.table("manual_entry").select("amount")
        .eq("clinic_id", clinic_id).eq("direction", "expense")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    m.misc_expense = _sum_amount(me_ex)

    return m


# ============================================================================
# 澤豐 — 12 變數聚合
# ============================================================================


def calculate_zefeng_monthly(
    sb, service_month: str, clinic_id: int, zepei_clinic_id: int
) -> ZefengMonthly:
    m = ZefengMonthly(service_month=service_month)
    next_month = _next_month(service_month)
    prev_month = _prev_month(service_month)

    # 玉山健保戶
    esun_id = _get_bank_account_id(sb, clinic_id, "健保戶")
    if esun_id:
        for tx in _fetch_bank_transactions(sb, esun_id, service_month):
            amt = tx["amount"]
            summary = (tx.get("summary") or "")
            if amt > 0:
                if "健保醫療給付" in summary or "健保" in summary:
                    m.nhi_inflow += amt
                else:
                    m.other_esun_in += amt
            else:
                a = -amt
                if "薪資" in summary:
                    m.salary_outflow_esun += a
                elif "健保" in summary or "勞保" in summary:
                    m.nhi_premium_outflow += a
                else:
                    m.other_esun_out += a

    # 中信進出戶（澤豐&個人混戶）
    ctbc_id = _get_bank_account_id(sb, clinic_id, "進出戶")
    if ctbc_id:
        # 月初餘額（前月最後一筆 balance；近似）
        first = (
            sb.table("bank_transactions").select("balance, transaction_date")
            .eq("account_id", ctbc_id)
            .lt("transaction_date", service_month)
            .order("transaction_date", desc=True).order("id", desc=True)
            .limit(1).execute().data
        )
        if first:
            m.x1_prev_balance = first[0].get("balance") or 0

        for tx in _fetch_bank_transactions(sb, ctbc_id, service_month):
            amt = tx["amount"]
            summary = (tx.get("summary") or "")
            cp = (tx.get("counterparty") or "")
            note = (tx.get("note") or "")
            channel = (tx.get("channel") or "")
            if amt > 0:
                # x2: 玉山健保戶轉入
                if "玉山" in cp or "0668" in cp:
                    m.x2_zefeng_inflow += amt
                # x6/x5/x7: 澤沛來的款項 — 暫合併為 x6（前月歸屬）
                elif "澤沛" in note or "澤沛" in cp or any(k in cp for k in ("0050", "1375")):
                    m.x6_fengpei_settle += amt
                # x8: 現金存入
                elif "現金" in summary or "存款機" in channel:
                    m.x8_zefeng_cash_revenue += amt
                # 其他
                else:
                    m.other_esun_in += amt
            # amt < 0 的支出在中信不分類（透支計算用，這裡略）

        # 月末餘額（最後一筆）
        last = (
            sb.table("bank_transactions").select("balance, transaction_date")
            .eq("account_id", ctbc_id)
            .gte("transaction_date", service_month)
            .lt("transaction_date", next_month)
            .order("transaction_date", desc=True).order("id", desc=True)
            .limit(1).execute().data
        )
        if last:
            m.x11_current_balance = last[0].get("balance") or 0

    # x3 澤豐現金支出（cash_expense clinic=澤豐 該月）
    cash_zf = (
        sb.table("cash_expense").select("amount")
        .eq("clinic_id", clinic_id).eq("accrual_month", service_month)
        .execute().data
    )
    m.x3_zefeng_cash_expense = _sum_amount(cash_zf)

    # x4 澤沛現金支出（澤豐代墊）
    cash_zp = (
        sb.table("cash_expense").select("amount")
        .eq("clinic_id", zepei_clinic_id).eq("accrual_month", service_month)
        .execute().data
    )
    m.x4_zepei_cash_expense_proxy = _sum_amount(cash_zp)

    # x12 澤豐合約支出
    contract_zf = (
        sb.table("contract_expense").select("amount")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    m.x12_zefeng_contract_expense = int(_sum_amount(contract_zf))

    # x9 編制外人力（謝松坊）— 從 staff_salary_summary 找
    offsite = (
        sb.table("staff_salary_summary").select("gross_salary, employee_label")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    for r in offsite:
        if "謝松坊" in (r.get("employee_label") or ""):
            m.x9_offsite_staff_pay += r.get("gross_salary") or 0

    # 一般員工薪資（不含 x9）
    m.staff_salary = sum(
        (r.get("gross_salary") or 0) for r in offsite
        if "謝松坊" not in (r.get("employee_label") or "")
    )

    # 醫師薪資
    ds = (
        sb.table("doctor_salary_monthly").select("total_salary")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    m.doctor_salary = _sum_amount(ds, "total_salary")

    # x10 手 KEY 非常規收支
    me_in = (
        sb.table("manual_entry").select("amount")
        .eq("clinic_id", clinic_id).eq("direction", "income")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    m.misc_income_x10 = _sum_amount(me_in)
    me_ex = (
        sb.table("manual_entry").select("amount")
        .eq("clinic_id", clinic_id).eq("direction", "expense")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    m.misc_expense_x10 = _sum_amount(me_ex)

    return m


# ============================================================================
# 高層 API
# ============================================================================


def calculate_both_clinics(sb, service_month: str):
    """一次算澤豐 + 澤沛"""
    clinics = sb.table("clinics").select("id, short_name").execute().data
    fz = next(c for c in clinics if c["short_name"] == "澤豐")
    fp = next(c for c in clinics if c["short_name"] == "澤沛")
    pl_fz = calculate_zefeng_monthly(sb, service_month, fz["id"], fp["id"])
    pl_fp = calculate_zepei_monthly(sb, service_month, fp["id"])
    return pl_fz, pl_fp


def list_available_months(sb) -> list[str]:
    """掃多 table 找有資料的月份"""
    months: set[str] = set()
    # 從 bank_transactions 找（最關鍵）
    try:
        rows = (
            sb.table("bank_transactions").select("transaction_date").execute().data
        )
        for r in rows:
            d = r.get("transaction_date")
            if d:
                months.add(d[:7] + "-01")
    except Exception:
        pass
    return sorted(months, reverse=True)
