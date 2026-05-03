"""
月度損益計算（Phase 4）

從 DB 即時聚合各種資料來源，產出兩家診所×月份×收入支出的損益表。

收入端：
  1. 健保收入（按 service_month 歸屬）— nhi_payment_notices.paid_amount
  2. 自費收入（不含掛號）— doctor_cash_monthly view.cash_total_excl_reg
  3. 掛號費 — doctor_cash_monthly.registration（也計入自費收入；兩家行為不同）
  4. 部分負擔 — doctor_outpatient_summary.copay_*
  5. 手 KEY 非常規收入 — manual_entry direction=income

支出端：
  1. 員工薪資 — staff_salary_summary.gross_salary
  2. 醫師薪資 — doctor_salary_monthly.total_salary（含跨支援代付）
  3. 現金支出 — cash_expense.amount（按 accrual_month）
  4. 合約支出 — contract_expense.amount（按 service_month）
  5. 支票支出 — check_expense.amount（共用檔，暫歸澤豐）
  6. 手 KEY 非常規支出 — manual_entry direction=expense

跨支援代付（豐沛金流）：另列項目，便於院長對帳。

⚠️ 簡化假設：
  - 支票支出全歸澤豐（兩家共用支票戶，院長以澤豐入帳；之後可細分）
  - 調貨金額暫 0（等 product_pricing trigger 算）
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from datetime import date, timedelta


@dataclass
class MonthlyIncome:
    nhi_paid: int = 0
    cash_self_pay: int = 0
    registration_fee: int = 0
    copay: int = 0
    misc_income: int = 0

    @property
    def total(self) -> int:
        return sum(getattr(self, f.name) for f in fields(self))


@dataclass
class MonthlyExpense:
    staff_salary: int = 0
    doctor_salary: int = 0
    cash_expense: int = 0
    contract_expense: int = 0
    check_expense: int = 0
    misc_expense: int = 0

    @property
    def total(self) -> int:
        return sum(getattr(self, f.name) for f in fields(self))


@dataclass
class MonthlyPL:
    clinic_id: int
    clinic_name: str
    service_month: str   # 'YYYY-MM-01'
    income: MonthlyIncome
    expense: MonthlyExpense
    cross_support_payable: int = 0   # 應付給對方診所的金額（豐沛金流）
    cross_support_receivable: int = 0  # 對方應付的金額（豐沛金流）

    @property
    def net_profit(self) -> int:
        return self.income.total - self.expense.total

    @property
    def fengpei_net(self) -> int:
        """豐沛金流淨值（正=對方欠我；負=我欠對方）"""
        return self.cross_support_receivable - self.cross_support_payable


def _next_month(service_month: str) -> str:
    d = date.fromisoformat(service_month)
    if d.month == 12:
        n = date(d.year + 1, 1, 1)
    else:
        n = date(d.year, d.month + 1, 1)
    return n.isoformat()


def _sum_field(rows: list[dict], field_name: str) -> int:
    return sum((r.get(field_name) or 0) for r in rows)


def calculate_monthly_pl(
    sb,
    service_month: str,
    clinic_id: int,
    clinic_name: str,
    is_zefeng: bool,
) -> MonthlyPL:
    """從 DB 聚合單一(診所×月份)的損益。"""
    next_month = _next_month(service_month)
    income = MonthlyIncome()
    expense = MonthlyExpense()

    # ─── 收入 ───
    # 1. 健保
    nhi = (
        sb.table("nhi_payment_notices")
        .select("paid_amount")
        .eq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    income.nhi_paid = _sum_field(nhi, "paid_amount")

    # 2-3. 自費（含/不含掛號分兩欄）
    cash = (
        sb.table("doctor_cash_monthly")
        .select("cash_total_excl_reg, registration")
        .eq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    income.cash_self_pay = _sum_field(cash, "cash_total_excl_reg")
    income.registration_fee = _sum_field(cash, "registration")

    # 4. 部分負擔（澤豐 copay_outpatient / 澤沛 copay_drug+trauma 都 sum）
    out = (
        sb.table("doctor_outpatient_summary")
        .select("copay_outpatient, copay_drug, copay_trauma")
        .eq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    income.copay = sum(
        (r.get("copay_outpatient") or 0)
        + (r.get("copay_drug") or 0)
        + (r.get("copay_trauma") or 0)
        for r in out
    )

    # 5. 手 KEY 非常規收入
    me_in = (
        sb.table("manual_entry")
        .select("amount")
        .eq("clinic_id", clinic_id)
        .eq("direction", "income")
        .gte("entry_date", service_month)
        .lt("entry_date", next_month)
        .execute().data
    )
    income.misc_income = _sum_field(me_in, "amount")

    # ─── 支出 ───
    # 1. 員工薪資（gross 都計，含主聘代付給對方）
    ss = (
        sb.table("staff_salary_summary")
        .select("gross_salary")
        .eq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    expense.staff_salary = _sum_field(ss, "gross_salary")

    # 2. 醫師薪資 — total_salary 已含主聘+跨支援
    ds = (
        sb.table("doctor_salary_monthly")
        .select("total_salary")
        .eq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    expense.doctor_salary = _sum_field(ds, "total_salary")

    # 3. 現金支出（按 accrual_month）
    ce = (
        sb.table("cash_expense")
        .select("amount")
        .eq("clinic_id", clinic_id)
        .eq("accrual_month", service_month)
        .execute().data
    )
    expense.cash_expense = _sum_field(ce, "amount")

    # 4. 合約支出
    ct = (
        sb.table("contract_expense")
        .select("amount")
        .eq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    expense.contract_expense = int(_sum_field(ct, "amount"))  # NUMERIC

    # 5. 支票支出（共用檔，暫歸澤豐）
    if is_zefeng:
        chk = (
            sb.table("check_expense")
            .select("amount")
            .eq("issue_month", service_month)
            .execute().data
        )
        expense.check_expense = _sum_field(chk, "amount")

    # 6. 手 KEY 非常規支出
    me_ex = (
        sb.table("manual_entry")
        .select("amount")
        .eq("clinic_id", clinic_id)
        .eq("direction", "expense")
        .gte("entry_date", service_month)
        .lt("entry_date", next_month)
        .execute().data
    )
    expense.misc_expense = _sum_field(me_ex, "amount")

    # ─── 跨支援豐沛金流 ───
    # A. 員工薪資代付（staff_salary_summary.paid_by_clinic_id）
    # B. 醫師薪資跨診所（從 doctor_salary_monthly 推；目前 schema 沒直接欄位）
    cross_payable = 0    # 我應付給對方
    cross_receivable = 0  # 對方應付給我

    # 員工：clinic=我 + paid_by=對方 → 對方代付 → 我欠對方
    ss_payable = (
        sb.table("staff_salary_summary")
        .select("gross_salary, paid_by_clinic_id")
        .eq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    for r in ss_payable:
        pid = r.get("paid_by_clinic_id")
        if pid and pid != clinic_id:
            cross_payable += r.get("gross_salary") or 0

    # 員工：clinic=對方 + paid_by=我 → 我代付 → 對方欠我
    ss_receivable = (
        sb.table("staff_salary_summary")
        .select("gross_salary, clinic_id")
        .eq("paid_by_clinic_id", clinic_id)
        .neq("clinic_id", clinic_id)
        .eq("service_month", service_month)
        .execute().data
    )
    for r in ss_receivable:
        cross_receivable += r.get("gross_salary") or 0

    return MonthlyPL(
        clinic_id=clinic_id,
        clinic_name=clinic_name,
        service_month=service_month,
        income=income,
        expense=expense,
        cross_support_payable=cross_payable,
        cross_support_receivable=cross_receivable,
    )


def calculate_both_clinics(
    sb,
    service_month: str,
) -> tuple[MonthlyPL, MonthlyPL]:
    """便利函式：一次算澤豐+澤沛"""
    clinics = sb.table("clinics").select("id, short_name").execute().data
    fz = next(c for c in clinics if c["short_name"] == "澤豐")
    fp = next(c for c in clinics if c["short_name"] == "澤沛")
    pl_fz = calculate_monthly_pl(
        sb, service_month, fz["id"], fz["short_name"], is_zefeng=True,
    )
    pl_fp = calculate_monthly_pl(
        sb, service_month, fp["id"], fp["short_name"], is_zefeng=False,
    )
    return pl_fz, pl_fp


def list_available_months(sb) -> list[str]:
    """掃描多個關鍵 table 找出有資料的服務月份（去重 + desc）"""
    months: set[str] = set()
    for tbl, col in [
        ("nhi_payment_notices", "service_month"),
        ("doctor_outpatient_summary", "service_month"),
        ("doctor_visit_stats", "service_month"),
        ("contract_expense", "service_month"),
    ]:
        try:
            rows = sb.table(tbl).select(col).execute().data
            months.update(r[col] for r in rows if r.get(col))
        except Exception:
            pass
    return sorted(months, reverse=True)
