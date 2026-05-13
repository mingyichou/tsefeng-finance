"""
周院長個人財富分析（Phase 5 — 院長 2026-05-13 規格）

澤豐&個人中信戶混了：澤豐中醫收支 + 周院長個人收支 + 澤沛代墊/還款。
用「反推法」分離 院長 personal flow：
  期初餘額 + 所有流入 - 已知 clinic 流出 - 期末餘額 = 院長個人 net 提領

公式（以 N 月帳）：
  n1 (澤豐中信 院長 personal outflow)
    = x1 + x2 - x3 - x4 + x5 + x6 + x7 + x8 - x9 + x10 - x12 - x11

變數定義：
  x1  = 中信 N 月初餘額（= N-1 月最後 balance）
  x2  = N 月 中信 inflow 來自 澤豐玉山 健保戶（counterparty 對應 澤豐玉山）
  x3  = N 月 cash_expense.accrual_month=N（澤豐零碎現金支出）
  x4  = N+1 月 澤沛中信 outflow note 含「現金支出/現支」反推
        （N 月 澤沛現金支出，N+1 月澤沛還款）
  x5  = N 月 澤沛中信 outflow note 含「現金支出/現支」
        （澤沛還 N-1 月代墊；由澤沛端 outflow 抓，澤豐中信備註可能缺）
  x6  = N 月 澤沛中信 outflow note 含「豐沛金流」
  x7  = N 月 澤沛中信 outflow note 含「合約」（澤沛還合約款）

  P.S. x5/x6/x7 雖然物理上是澤豐中信 inflow，但備註只標於澤沛端，
       故由澤沛 outflow 抓金額（兩側必相等）。
  x8  = N 月 中信 inflow channel/summary 含「現金/存款機」+ manual_annotation 配對
  x9  = N-1 月 staff_salary_summary 編制外名單薪資（N 月 cash 支付）
  x10 = N 月 manual_entry(clinic=澤豐, income - expense)
  x11 = 中信 N 月底餘額
  x12 = N 月 contract_expense.service_month=N（澤豐合約支出）
  x13 = N-1 月 doctor_salary_monthly 周明毅 total_salary 兩院總和
        (N 月實帳支付的薪水 = N-1 月服務月薪資)

  n2 (澤豐玉山 院長 personal outflow)
    = 澤豐玉山 N 月 outflow 對方含 zhou_personal_accounts（玉山 / 中信戶尾碼）

  總支出 N  = n1 + n2
  支票 P    = check_expense.issue_month=N 全部
  私人支出  = N - P
  收入(x13) = doctor_salary_monthly 周明毅 total_salary (兩院)
  透支      = 私人支出 - x13
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


XIE_SONGFANG_KEYWORDS = ("謝松坊",)  # 編制外人力


def _next_month(month: str) -> str:
    d = date.fromisoformat(month)
    return (date(d.year + 1, 1, 1).isoformat() if d.month == 12
            else date(d.year, d.month + 1, 1).isoformat())


def _prev_month(month: str) -> str:
    d = date.fromisoformat(month)
    return (date(d.year - 1, 12, 1).isoformat() if d.month == 1
            else date(d.year, d.month - 1, 1).isoformat())


def _normalize(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", text or "")


def _digits_only(s: str) -> str:
    return "".join(c for c in (s or "") if c.isalnum())


def _is_fengpei(note: str) -> bool:
    s = _normalize(note)
    return any(k in s for k in (
        "豐沛金流", "沛豐金流", "澤沛金流", "沛to豐", "沛 to 豐",
    ))


def _is_zepei_cash_repay(note: str) -> bool:
    s = _normalize(note)
    return "現金支出" in s or "現支" in s


def _is_zepei_contract(note: str) -> bool:
    return "合約" in _normalize(note)


def _read_settings_list(sb, key: str, default: list[str]) -> list[str]:
    import json
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


def _is_personal_account(counterparty: str, accounts: list[str]) -> bool:
    cp = _digits_only(counterparty)
    for acc in accounts:
        if acc and acc.lstrip("0") and acc.lstrip("0") in cp:
            return True
    return False


# ─── Dataclass ─────────────────────────────────────────────


@dataclass
class ZhouMonthlyFinance:
    """周院長 N 月財富分析。"""
    service_month: str

    # 中信 (n1 公式 12 變數)
    x1_prev_balance: int = 0
    x2_clinic_transfer_in: int = 0
    x3_zefeng_cash_expense: int = 0
    x4_zepei_cash_advance: int = 0
    x5_zepei_cash_repay: int = 0
    x6_fengpei_in: int = 0
    x7_zepei_contract_in: int = 0
    x8_zefeng_cash_in: int = 0
    x9_external_staff_salary: int = 0
    x10_manual_net: int = 0  # income - expense
    x11_current_balance: int = 0
    x12_zefeng_contract_expense: int = 0

    # 玉山個人轉出（n2）
    n2_items: list = field(default_factory=list)

    # x13 院長收入
    x13_zhou_salary: int = 0
    x13_items: list = field(default_factory=list)

    # 支票
    p_check_items: list = field(default_factory=list)

    # 手KEY 個人 memo_only 大筆紀錄（不入計算）
    personal_memo_items: list = field(default_factory=list)

    # 透明明細（驗證用）
    x2_items: list = field(default_factory=list)
    x4_items: list = field(default_factory=list)
    x5_items: list = field(default_factory=list)
    x6_items: list = field(default_factory=list)
    x7_items: list = field(default_factory=list)
    x8_items: list = field(default_factory=list)

    @property
    def n1(self) -> int:
        """中信戶 院長個人支出（含轉到玉山個人戶等）"""
        return (
            self.x1_prev_balance
            + self.x2_clinic_transfer_in
            - self.x3_zefeng_cash_expense
            - self.x4_zepei_cash_advance
            + self.x5_zepei_cash_repay
            + self.x6_fengpei_in
            + self.x7_zepei_contract_in
            + self.x8_zefeng_cash_in
            - self.x9_external_staff_salary
            + self.x10_manual_net
            - self.x12_zefeng_contract_expense
            - self.x11_current_balance
        )

    @property
    def n2(self) -> int:
        return sum(int(it.get("amount") or 0) for it in self.n2_items)

    @property
    def total_expense(self) -> int:
        """周院長個人總支出 N = n1 + n2"""
        return self.n1 + self.n2

    @property
    def p_check_total(self) -> int:
        return sum(int(it.get("amount") or 0) for it in self.p_check_items)

    @property
    def private_expense(self) -> int:
        """周院長私人支出 = N - 支票支出"""
        return self.total_expense - self.p_check_total

    @property
    def overdraft(self) -> int:
        """透支 = 私人支出 - 院長收入"""
        return self.private_expense - self.x13_zhou_salary


# ─── Supabase helpers ──────────────────────────────────────


def _get_bank_account_id(sb, clinic_id: int, account_type: str) -> int | None:
    resp = (
        sb.table("bank_accounts").select("id")
        .eq("clinic_id", clinic_id).eq("account_type", account_type)
        .execute().data
    )
    return resp[0]["id"] if resp else None


def _fetch_tx(sb, account_id: int, month_iso: str) -> list[dict]:
    nm = _next_month(month_iso)
    return (
        sb.table("bank_transactions")
        .select(
            "transaction_date, summary, amount, counterparty, "
            "channel, note, memo_month, balance"
        )
        .eq("account_id", account_id)
        .gte("transaction_date", month_iso)
        .lt("transaction_date", nm)
        .order("transaction_date").order("id")
        .execute().data
    )


# ─── 計算 ──────────────────────────────────────────────────


def calculate_zhou_monthly(sb, service_month: str) -> ZhouMonthlyFinance:
    z = ZhouMonthlyFinance(service_month=service_month)
    sm = service_month
    next_m = _next_month(sm)
    prev_m = _prev_month(sm)

    # 診所 id
    clinics = sb.table("clinics").select("id, short_name").execute().data
    fz = next(c for c in clinics if c["short_name"] == "澤豐")
    fp = next(c for c in clinics if c["short_name"] == "澤沛")
    fz_id = fz["id"]
    fp_id = fp["id"]

    fz_ctbc_id = _get_bank_account_id(sb, fz_id, "進出戶")
    fz_esun_id = _get_bank_account_id(sb, fz_id, "健保戶")
    fp_ctbc_id = _get_bank_account_id(sb, fp_id, "進出戶")

    # 從 system_settings 讀 院長個人帳號
    zhou_accounts = _read_settings_list(
        sb, "zhou_personal_accounts",
        ["0668979072975", "137540125004"],
    )
    external_names = _read_settings_list(
        sb, "external_staff_names", ["謝松坊"],
    )

    # ===== 中信 N 月 transactions =====
    n_tx = _fetch_tx(sb, fz_ctbc_id, sm) if fz_ctbc_id else []

    # x1 前月底餘額（中信）
    if fz_ctbc_id:
        prev_last = (
            sb.table("bank_transactions").select("balance, transaction_date")
            .eq("account_id", fz_ctbc_id).lt("transaction_date", sm)
            .order("transaction_date", desc=True).order("id", desc=True)
            .limit(1).execute().data
        )
        if prev_last:
            z.x1_prev_balance = int(prev_last[0].get("balance") or 0)

        # x11 當月底餘額
        if n_tx:
            z.x11_current_balance = int(n_tx[-1].get("balance") or 0)
        else:
            # 若 N 月無交易，沿用 x1
            z.x11_current_balance = z.x1_prev_balance

    # x2 玉山健保戶轉入：澤豐中信 inflow 摘要/備註含「玉山」
    # 實務上：澤豐玉山 → 澤豐中信 跨行轉帳，摘要常見「ＡＴＭ跨行轉」
    for tx in n_tx:
        amt = tx.get("amount") or 0
        if amt <= 0:
            continue
        summary = tx.get("summary") or ""
        note = tx.get("note") or ""
        if ("玉山" in summary) or ("玉山" in note):
            z.x2_clinic_transfer_in += int(amt)
            z.x2_items.append(_to_item(tx))

    # x5/x6/x7：從澤沛中信 N 月 outflow 的 note 分類抓金額
    # （澤豐中信側備註常缺；澤沛端 outflow 必定一一對應澤豐 inflow，金額相等）
    if fp_ctbc_id:
        for tx in _fetch_tx(sb, fp_ctbc_id, sm):
            amt = tx.get("amount") or 0
            if amt >= 0:
                continue
            note = tx.get("note") or ""
            abs_amt = -int(amt)
            if _is_fengpei(note):
                z.x6_fengpei_in += abs_amt
                z.x6_items.append(_to_item(tx))
            elif _is_zepei_cash_repay(note):
                z.x5_zepei_cash_repay += abs_amt
                z.x5_items.append(_to_item(tx))
            elif _is_zepei_contract(note):
                z.x7_zepei_contract_in += abs_amt
                z.x7_items.append(_to_item(tx))

    # x8 現金存入需要 manual_annotation 配對（同 月度實帳金流 邏輯）
    ann_cash = (
        sb.table("manual_annotation")
        .select("entry_date, amount, description, form, clinic_id, scope, account")
        .eq("scope", "診所").in_("form", ["存現", "轉入"])
        .eq("account", "澤豐&個人中信")
        .gte("entry_date", sm).lt("entry_date", next_m)
        .execute().data
    )
    ann_keys = {(r["entry_date"], int(r["amount"] or 0)) for r in ann_cash}
    ann_amt_only: dict = {}
    for r in ann_cash:
        ann_amt_only.setdefault(int(r["amount"] or 0), []).append(r)

    for tx in n_tx:
        amt = tx.get("amount") or 0
        if amt <= 0:
            continue
        summary = tx.get("summary") or ""
        channel = tx.get("channel") or ""
        note = tx.get("note") or ""
        if "玉山" in summary or "玉山" in note:
            continue  # 已歸 x2
        # 現金存入候選
        if not ("現金" in summary or "存款機" in channel):
            continue
        tx_date = tx.get("transaction_date")
        amt_int = int(amt)
        if (tx_date, amt_int) in ann_keys:
            z.x8_zefeng_cash_in += amt_int
            z.x8_items.append(_to_item(tx))
        elif len(ann_amt_only.get(amt_int, [])) == 1:
            z.x8_zefeng_cash_in += amt_int
            z.x8_items.append(_to_item(tx))
        # 無對應 manual_annotation 視為個人存款，不算 x8

    # x3 澤豐現金支出（cash_expense.accrual_month=N）
    cash = (
        sb.table("cash_expense").select("amount")
        .eq("clinic_id", fz_id).eq("accrual_month", sm)
        .execute().data
    )
    z.x3_zefeng_cash_expense = sum(int(r.get("amount") or 0) for r in cash)

    # x4 澤沛現金支出（由 N+1 月 澤沛中信 outflow 註記="現金支出" 反推）
    if fp_ctbc_id:
        for tx in _fetch_tx(sb, fp_ctbc_id, next_m):
            amt = tx.get("amount") or 0
            if amt >= 0:
                continue
            if _is_zepei_cash_repay(tx.get("note") or ""):
                z.x4_zepei_cash_advance += -int(amt)
                z.x4_items.append(_to_item(tx))

    # x9 編制外人力薪資（N-1 月 service_month，N 月 cash 支付）
    ss = (
        sb.table("staff_salary_summary")
        .select("employee_label, gross_salary")
        .eq("clinic_id", fz_id).eq("service_month", prev_m)
        .execute().data
    )
    for r in ss:
        label = r.get("employee_label") or ""
        if any(n in label for n in external_names):
            z.x9_external_staff_salary += int(r.get("gross_salary") or 0)

    # x10 手 KEY 非常規（澤豐）net
    me_in = (
        sb.table("manual_entry").select("amount")
        .eq("clinic_id", fz_id).eq("direction", "income")
        .gte("entry_date", sm).lt("entry_date", next_m)
        .execute().data
    )
    me_out = (
        sb.table("manual_entry").select("amount")
        .eq("clinic_id", fz_id).eq("direction", "expense")
        .gte("entry_date", sm).lt("entry_date", next_m)
        .execute().data
    )
    z.x10_manual_net = (
        sum(int(r.get("amount") or 0) for r in me_in)
        - sum(int(r.get("amount") or 0) for r in me_out)
    )

    # x12 澤豐合約支出
    ct = (
        sb.table("contract_expense").select("amount")
        .eq("clinic_id", fz_id).eq("service_month", sm)
        .execute().data
    )
    z.x12_zefeng_contract_expense = sum(
        int(round(float(r.get("amount") or 0))) for r in ct
    )

    # x13 周明毅薪資（兩院總和）— N 月實帳支付的是 N-1 月服務月薪資
    zhou_doc = (
        sb.table("doctors").select("id").eq("name", "周明毅").execute().data
    )
    if zhou_doc:
        zhou_id = zhou_doc[0]["id"]
        zs = (
            sb.table("doctor_salary_monthly")
            .select("total_salary, clinic_id, service_month")
            .eq("doctor_id", zhou_id).eq("service_month", prev_m)
            .execute().data
        )
        for r in zs:
            amt = int(r.get("total_salary") or 0)
            z.x13_zhou_salary += amt
            z.x13_items.append({
                "service_month": r.get("service_month"),
                "clinic_id": r.get("clinic_id"),
                "amount": amt,
            })

    # n2 玉山戶 outflow 到院長個人帳號（玉山+中信尾段都算）
    if fz_esun_id:
        for tx in _fetch_tx(sb, fz_esun_id, sm):
            amt = tx.get("amount") or 0
            if amt >= 0:
                continue
            cp = tx.get("counterparty") or ""
            if _is_personal_account(cp, zhou_accounts):
                z.n2_items.append({
                    "transaction_date": tx.get("transaction_date"),
                    "summary": tx.get("summary") or "",
                    "counterparty": cp,
                    "amount": -int(amt),
                })

    # 支票
    chk = (
        sb.table("check_expense")
        .select("issue_month, vendor, bank, amount, note")
        .eq("issue_month", sm).execute().data
    )
    for r in chk:
        z.p_check_items.append({
            "vendor": r.get("vendor") or "",
            "bank": r.get("bank") or "",
            "amount": int(r.get("amount") or 0),
            "note": r.get("note") or "",
        })

    # 個人 memo_only 大筆紀錄
    pm = (
        sb.table("manual_annotation")
        .select("entry_date, amount, description, form, account")
        .eq("scope", "個人").eq("category", "memo_only")
        .gte("entry_date", sm).lt("entry_date", next_m)
        .execute().data
    )
    for r in pm:
        z.personal_memo_items.append({
            "entry_date": r.get("entry_date"),
            "form": r.get("form") or "",
            "account": r.get("account") or "",
            "amount": int(r.get("amount") or 0),
            "description": r.get("description") or "",
        })

    return z


def _to_item(tx: dict) -> dict:
    amt = tx.get("amount") or 0
    return {
        "transaction_date": tx.get("transaction_date"),
        "summary": tx.get("summary") or "",
        "note": tx.get("note") or "",
        "counterparty": tx.get("counterparty") or "",
        "channel": tx.get("channel") or "",
        "amount": int(amt) if amt >= 0 else -int(amt),
    }


def list_available_months(sb) -> list[str]:
    """有 中信 交易資料的月份；過濾 < 2026-01。"""
    from data_processor.monthly_pl import MIN_SERVICE_MONTH
    months: set[str] = set()
    try:
        rows = sb.table("bank_transactions").select(
            "transaction_date").execute().data
        for r in rows:
            d = r.get("transaction_date")
            if d:
                months.add(d[:7] + "-01")
    except Exception:
        pass
    return sorted(
        (m for m in months if m >= MIN_SERVICE_MONTH),
        reverse=True,
    )
