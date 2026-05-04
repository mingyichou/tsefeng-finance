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
    """
    澤沛實帳收支（院長 2026-05-04 重做）
    完全記錄帳戶內容，每一筆都歸類到適當項目，不忽略任何 transaction。
    澤沛沒有混到私人財務，全部歸屬診所。
    """
    service_month: str
    # 玉山健保戶
    nhi_inflow: int = 0           # 健保醫療給付
    other_esun_in: int = 0        # 玉山其他入帳（極少見）
    salary_outflow_esun: int = 0   # 薪資轉帳
    other_esun_out: int = 0       # 玉山其他支出
    # 中信進出戶（全部記錄）
    cross_inflow: int = 0          # 澤豐→澤沛 跨診所匯入
    cash_deposit: int = 0          # 現金存入
    other_ctbc_in: int = 0         # 其他中信入帳（廠商匯款等）
    contract_outflow: int = 0      # 轉合約廠商
    cross_outflow: int = 0         # 澤沛→澤豐 跨診所匯出
    rent_outflow: int = 0          # 房租支出
    consulting_outflow: int = 0    # 管理顧問費
    other_ctbc_out: int = 0        # 其他中信支出
    # 系統推算（補充）
    cash_expense_total: int = 0    # 該月 cash_expense（逐筆現金支出）
    contract_expense_total: int = 0  # 該月 contract_expense（合約彙總）
    # 手 KEY
    misc_income: int = 0
    misc_expense: int = 0

    @property
    def total_income(self) -> int:
        return (
            self.nhi_inflow + self.other_esun_in
            + self.cross_inflow + self.cash_deposit + self.other_ctbc_in
            + self.misc_income
        )

    @property
    def total_expense(self) -> int:
        # 銀行直接扣款 + 系統彙總（cash/contract_expense_total）+ 手KEY 支出
        # 注意：銀行扣款（薪資/合約轉帳/跨診所匯出/房租/顧問費/其他）+ 系統彙總
        return (
            self.salary_outflow_esun + self.other_esun_out
            + self.contract_outflow + self.cross_outflow
            + self.rent_outflow + self.consulting_outflow + self.other_ctbc_out
            + self.cash_expense_total
            + self.misc_expense
        )

    @property
    def net(self) -> int:
        return self.total_income - self.total_expense


@dataclass
class ZefengMonthly:
    """
    澤豐實帳收支（依院長 2026-05-04 簡化）
    只算明確屬於澤豐中醫診所的收支：
      收入：玉山「健保醫療給付」+ 中信 x6/x8 + x10 income
      支出：玉山「薪資轉帳」+「健保扣繳」+ x3/x9/x10/x12

    不算入：玉山轉到周院長 808/0668979072975 的個人轉帳；玉山其他不明入帳；
            staff_salary_summary 與 doctor_salary_monthly（避免雙重計算）
    """
    service_month: str
    # 玉山健保戶（明確屬診所的收支）
    nhi_inflow: int = 0          # 健保醫療給付
    salary_outflow_esun: int = 0  # 玉山薪資轉帳
    nhi_premium_outflow: int = 0  # 玉山健保扣繳/勞保
    # 中信進出戶（明確屬診所收入）
    x6_zepei_to_zefeng: int = 0  # 澤沛→澤豐金流（含 x5/x6/x7 來款）
    x8_zefeng_cash_revenue: int = 0  # 現金存入（前月診所現金收入）
    misc_income_x10: int = 0     # 手 KEY 非常規收入
    misc_expense_x10: int = 0    # 手 KEY 非常規支出
    # 中信餘額（資訊用，不算入收支）
    x1_prev_balance: int = 0
    x11_current_balance: int = 0
    # 隱形支出（推算）
    x3_zefeng_cash_expense: int = 0      # 澤豐現金支出（cash_expense）
    x9_offsite_staff_pay: int = 0        # 編制外人力（謝松坊）
    x12_zefeng_contract_expense: int = 0  # 澤豐合約支出

    @property
    def total_income(self) -> int:
        return (
            self.nhi_inflow + self.x6_zepei_to_zefeng
            + self.x8_zefeng_cash_revenue + self.misc_income_x10
        )

    @property
    def total_expense(self) -> int:
        return (
            self.salary_outflow_esun + self.nhi_premium_outflow
            + self.x3_zefeng_cash_expense
            + self.x9_offsite_staff_pay
            + self.x12_zefeng_contract_expense
            + self.misc_expense_x10
        )

    @property
    def net(self) -> int:
        return self.total_income - self.total_expense


@dataclass
class CheckExpenseMonth:
    """支票支出（兩家共用，獨立項目；不入合計趨勢圖）"""
    service_month: str
    total: int = 0
    by_vendor: dict = field(default_factory=dict)
    by_bank: dict = field(default_factory=dict)
    raw_items: list = field(default_factory=list)


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

    # ─── 玉山健保戶：每筆都記 ───
    esun_id = _get_bank_account_id(sb, clinic_id, "健保戶")
    if esun_id:
        for tx in _fetch_bank_transactions(sb, esun_id, service_month):
            amt = tx["amount"]
            summary = (tx.get("summary") or "")
            if amt > 0:
                if "健保醫療給付" in summary:
                    m.nhi_inflow += amt
                else:
                    m.other_esun_in += amt  # 不忽略
            else:
                a = -amt
                if "薪資" in summary:
                    m.salary_outflow_esun += a
                else:
                    m.other_esun_out += a  # 不忽略（手續費等也記）

    # ─── 中信進出戶：每筆都記 ───
    ctbc_id = _get_bank_account_id(sb, clinic_id, "進出戶")
    if ctbc_id:
        for tx in _fetch_bank_transactions(sb, ctbc_id, service_month):
            amt = tx["amount"]
            summary = (tx.get("summary") or "")
            cp = (tx.get("counterparty") or "")
            note = (tx.get("note") or "")
            blob = f"{note}|{cp}|{summary}"
            if amt > 0:
                if "澤豐" in blob:
                    m.cross_inflow += amt
                elif "現金" in summary or "存款機" in summary:
                    m.cash_deposit += amt
                else:
                    m.other_ctbc_in += amt  # 廠商匯款等
            else:
                a = -amt
                if "澤豐" in blob:
                    m.cross_outflow += a
                elif "房租" in note or "房租" in cp:
                    m.rent_outflow += a
                elif "管理" in note or "顧問" in note or "管理費" in note:
                    m.consulting_outflow += a
                elif any(k in blob for k in (
                    "莊松榮", "港香蘭", "天一", "駿賀", "大墩",
                    "順天", "簽口", "力至高", "科達",
                )):
                    m.contract_outflow += a
                else:
                    m.other_ctbc_out += a  # 不忽略

    # cash_expense / contract_expense 當月彙總（澤沛清楚的支出主軸）
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

    # ─── 玉山健保戶（只算明確屬診所的）───
    esun_id = _get_bank_account_id(sb, clinic_id, "健保戶")
    if esun_id:
        for tx in _fetch_bank_transactions(sb, esun_id, service_month):
            amt = tx["amount"]
            summary = (tx.get("summary") or "")
            cp = (tx.get("counterparty") or "")
            if amt > 0:
                # 只算「健保醫療給付」屬於澤豐診所收入
                if "健保醫療給付" in summary:
                    m.nhi_inflow += amt
                # 其他入帳（如轉入、退款）不算 — 視為個人或不明
            else:
                # 排除轉到周院長個人 808/0000668979072975
                if "808" in cp and "0668979072975" in cp:
                    continue
                # 也排除明顯的個人轉帳關鍵字（保守）
                if "周明毅" in cp or "周院長" in cp:
                    continue
                a = -amt
                if "薪資" in summary:
                    m.salary_outflow_esun += a
                elif "健保" in summary or "勞保" in summary or "代繳" in summary:
                    m.nhi_premium_outflow += a
                # 其他玉山支出（如手續費等小額）忽略

    # ─── 中信進出戶（澤豐&個人混戶；只取明確屬診所的）───
    ctbc_id = _get_bank_account_id(sb, clinic_id, "進出戶")
    if ctbc_id:
        # 月初餘額（前月最後一筆 balance；資訊用）
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
                # x6 澤沛→澤豐金流（含 x5/x6/x7 來款）
                if "澤沛" in note or "澤沛" in cp or any(k in cp for k in ("0050", "1375")):
                    m.x6_zepei_to_zefeng += amt
                # x8 現金存入（前月診所現金收入）
                elif "現金" in summary or "存款機" in channel:
                    m.x8_zefeng_cash_revenue += amt
                # x2 玉山轉入屬診所內部周轉（不算收入）— 略過
                # 其他入帳屬個人 — 略過

        # 月末餘額
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

    # ─── 隱形支出 ───
    # x3 澤豐現金支出
    cash_zf = (
        sb.table("cash_expense").select("amount")
        .eq("clinic_id", clinic_id).eq("accrual_month", service_month)
        .execute().data
    )
    m.x3_zefeng_cash_expense = _sum_amount(cash_zf)

    # x12 澤豐合約支出
    contract_zf = (
        sb.table("contract_expense").select("amount")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    m.x12_zefeng_contract_expense = int(_sum_amount(contract_zf))

    # x9 編制外人力（謝松坊）
    offsite = (
        sb.table("staff_salary_summary").select("gross_salary, employee_label")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    for r in offsite:
        if "謝松坊" in (r.get("employee_label") or ""):
            m.x9_offsite_staff_pay += r.get("gross_salary") or 0

    # ─── 手 KEY 非常規 ───
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


def calculate_check_expense_month(sb, service_month: str) -> CheckExpenseMonth:
    """計算單月支票支出（兩家共用，獨立項目）"""
    rows = (
        sb.table("check_expense")
        .select("vendor, amount, bank, note")
        .eq("issue_month", service_month)
        .execute().data
    )
    m = CheckExpenseMonth(service_month=service_month)
    for r in rows:
        amt = r.get("amount") or 0
        m.total += amt
        v = r.get("vendor") or "其他"
        b = r.get("bank") or "未知"
        m.by_vendor[v] = m.by_vendor.get(v, 0) + amt
        m.by_bank[b] = m.by_bank.get(b, 0) + amt
        m.raw_items.append(r)
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
