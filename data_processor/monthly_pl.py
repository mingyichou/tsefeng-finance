"""
月度實帳收支（v4 — 院長 2026-05-05 大前提）

3 個收支主體：周明毅院長個人 / 澤豐中醫診所 / 澤沛中醫診所。
本檔只計「澤豐」「澤沛」兩家診所的實帳收支。
周院長個人財務、跨月實質損益（屬前月）— 之後另做。

模式：N 月帳上實際發生的款項記在 N 月。
跨月歸屬（屬前月）只在 item dict 用 attribution_month 標註，
**不**用於本月合計。

澤豐：
  收入 = 玉山逐筆入帳 + x6 豐沛金流入帳 + x8 現金存入 + x10 手 KEY 收入
  支出 = 玉山逐筆出帳（排除轉到 668979072975 周院長個人 + 0347940007803
         澤豐自家中信）+ x3 澤豐現金支出 + x9 謝松坊薪資 + x10 手 KEY 支出
         + x12 澤豐合約支出 + x13 周院長薪資

澤沛（沒混到私人財務）：
  收入 = 玉山逐筆入帳 + 中信逐筆入帳 + x10 手 KEY 收入
  支出 = 玉山逐筆出帳 + 中信逐筆出帳 + x10 手 KEY 支出
  中信 3 筆固定 settle 保留 settle_kind 分類（x5/x6/x7），
  月份用 transaction_date 當月（不再用標籤月歸屬）。

不在澤豐實帳：
  x2（玉山→中信轉入，已被排除規則涵蓋）
  x5（澤沛→周院長現金支出結算）— 屬周院長個人，由澤沛端記出
  x7（澤沛→周院長合約結算）— 屬周院長個人，由澤沛端記出
  x4（澤沛 N-1 月現金支出，由周院長代墊）— 屬周院長個人，全系統不記
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


# ─── 帳號 / 標籤辨識 ──────────────────────────────────────
ZEPEI_CTBC_TAIL = "137540125004"          # 澤沛中信帳號末段
ZHOU_PERSONAL_TAIL = "0668979072975"      # 周院長個人帳尾號（澤豐玉山戶要排除）
ZEFENG_CTBC_TAIL = "0347940007803"        # 澤豐自家中信帳號末段（內部移轉，不記）


def _normalize(text: str) -> str:
    """全形 → 半形 + 英數標準化（NFKC）— 處理「沛２月」「沛ｔｏ豐」等"""
    import unicodedata
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text)


def _zepei_settle_kind(text: str) -> str | None:
    """
    判斷澤沛中信出帳是哪一種固定 settle（保留分類用，**不**判斷月份）：
      - x5_cash:    含「現金支出」
      - x6_fengpei: 含「豐沛金流」或「沛 to 豐」（含全形ｔｏ）
      - x7_contract: 含「合約」
    其他（房租、傳單等）回 None
    """
    if not text:
        return None
    s = _normalize(text)
    if "豐沛金流" in s or "沛to豐" in s or "沛 to 豐" in s:
        return "x6_fengpei"
    if "現金支出" in s:
        return "x5_cash"
    if "合約" in s:
        return "x7_contract"
    return None


def _digits_only(s: str) -> str:
    return "".join(c for c in (s or "") if c.isalnum())


def _is_zhou_personal_transfer(counterparty: str) -> bool:
    """澤豐玉山戶對方帳號是周院長個人（含 668979072975）— 出入帳都排除"""
    return ZHOU_PERSONAL_TAIL.lstrip("0") in _digits_only(counterparty)


def _is_zefeng_ctbc_internal(counterparty: str) -> bool:
    """澤豐玉山戶對方帳號是澤豐自家中信（內部移轉）— 出入帳都排除"""
    return ZEFENG_CTBC_TAIL.lstrip("0") in _digits_only(counterparty)


def _extract_attr_month_from_desc(desc: str, fallback: str | None = None) -> str | None:
    """
    從 manual_annotation.description 或 csv note 抓「11502 / 11504 / 沛N月」格式
    並轉成 ISO 月份字串（"2026-02-01"）。抓不到回 fallback。
    """
    if not desc:
        return fallback
    import re
    s = _normalize(desc)
    # 「11502」「11504」格式（民國年+月）
    m = re.search(r"11(\d{2})", s)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            return f"2026-{mo:02d}-01"
    # 「沛 N 月」「N 月」格式 — 推測為民國 115 年
    m = re.search(r"(?:沛|澤沛)?\s*(\d{1,2})\s*月", s)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            return f"2026-{mo:02d}-01"
    return fallback


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class ZepeiMonthly:
    """澤沛實帳（玉山+中信全記，含 3 筆 settle 分類）"""
    service_month: str

    # 玉山健保戶（逐筆，amount 存正值；direction 看放在 inflow/outflow list）
    esun_inflow_items: list = field(default_factory=list)
    esun_outflow_items: list = field(default_factory=list)
    # 中信進出戶（逐筆；outflow 含 settle_kind 標註）
    ctbc_inflow_items: list = field(default_factory=list)
    ctbc_outflow_items: list = field(default_factory=list)
    # 手 KEY x10
    x10_income_items: list = field(default_factory=list)
    x10_expense_items: list = field(default_factory=list)

    @property
    def esun_inflow_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.esun_inflow_items)

    @property
    def esun_outflow_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.esun_outflow_items)

    @property
    def ctbc_inflow_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.ctbc_inflow_items)

    @property
    def ctbc_outflow_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.ctbc_outflow_items)

    @property
    def x10_income_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.x10_income_items)

    @property
    def x10_expense_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.x10_expense_items)

    # 3 筆 settle 細分（已含於 ctbc_outflow_total，僅供識別顯示）
    @property
    def cash_settle_outflow(self) -> int:
        return sum(
            it.get("amount", 0) for it in self.ctbc_outflow_items
            if it.get("settle_kind") == "x5_cash"
        )

    @property
    def fengpei_outflow(self) -> int:
        return sum(
            it.get("amount", 0) for it in self.ctbc_outflow_items
            if it.get("settle_kind") == "x6_fengpei"
        )

    @property
    def contract_settle_outflow(self) -> int:
        return sum(
            it.get("amount", 0) for it in self.ctbc_outflow_items
            if it.get("settle_kind") == "x7_contract"
        )

    @property
    def total_income(self) -> int:
        return (
            self.esun_inflow_total + self.ctbc_inflow_total
            + self.x10_income_total
        )

    @property
    def total_expense(self) -> int:
        return (
            self.esun_outflow_total + self.ctbc_outflow_total
            + self.x10_expense_total
        )

    @property
    def net(self) -> int:
        return self.total_income - self.total_expense


@dataclass
class ZefengMonthly:
    """澤豐實帳（玉山逐筆 — 排除個人轉帳；中信只取 x6/x8；隱形 x3/x9/x12/x13；x10 手 KEY）"""
    service_month: str

    # 玉山健保戶（已排除：→ 周院長個人 / → 澤豐自家中信）
    esun_inflow_items: list = field(default_factory=list)
    esun_outflow_items: list = field(default_factory=list)

    # 中信進出戶（混戶 — 只抓 x6 + x8）
    x6_fengpei_settle: int = 0
    x6_items: list = field(default_factory=list)
    x8_zefeng_cash_revenue: int = 0
    x8_items: list = field(default_factory=list)

    # 中信餘額（資訊用，不入合計）
    x1_prev_balance: int = 0
    x11_current_balance: int = 0

    # 隱形支出
    x3_zefeng_cash_expense: int = 0
    x3_items: list = field(default_factory=list)
    x9_offsite_staff_pay: int = 0
    x9_items: list = field(default_factory=list)
    x12_zefeng_contract_expense: int = 0
    x12_items: list = field(default_factory=list)
    x13_zhou_doctor_salary: int = 0
    x13_items: list = field(default_factory=list)

    # 手 KEY x10
    x10_income_items: list = field(default_factory=list)
    x10_expense_items: list = field(default_factory=list)

    @property
    def esun_inflow_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.esun_inflow_items)

    @property
    def esun_outflow_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.esun_outflow_items)

    @property
    def x10_income_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.x10_income_items)

    @property
    def x10_expense_total(self) -> int:
        return sum(it.get("amount", 0) for it in self.x10_expense_items)

    @property
    def total_income(self) -> int:
        return (
            self.esun_inflow_total
            + self.x6_fengpei_settle
            + self.x8_zefeng_cash_revenue
            + self.x10_income_total
        )

    @property
    def total_expense(self) -> int:
        return (
            self.esun_outflow_total
            + self.x3_zefeng_cash_expense
            + self.x9_offsite_staff_pay
            + self.x10_expense_total
            + self.x12_zefeng_contract_expense
            + self.x13_zhou_doctor_salary
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
        return date(d.year + 1, 1, 1).isoformat()
    return date(d.year, d.month + 1, 1).isoformat()


def _prev_month(service_month: str) -> str:
    d = date.fromisoformat(service_month)
    if d.month == 1:
        return date(d.year - 1, 12, 1).isoformat()
    return date(d.year, d.month - 1, 1).isoformat()


def _sum_amount(rows: list[dict], field_name: str = "amount") -> int:
    return sum((r.get(field_name) or 0) for r in rows)


def _get_bank_account_id(sb, clinic_id: int, account_type: str) -> int | None:
    resp = (
        sb.table("bank_accounts").select("id")
        .eq("clinic_id", clinic_id).eq("account_type", account_type)
        .execute().data
    )
    return resp[0]["id"] if resp else None


def _fetch_bank_transactions(sb, account_id: int, service_month: str) -> list[dict]:
    next_month = _next_month(service_month)
    return (
        sb.table("bank_transactions")
        .select("transaction_date, summary, amount, counterparty, channel, note, memo_month")
        .eq("account_id", account_id)
        .gte("transaction_date", service_month)
        .lt("transaction_date", next_month)
        .order("transaction_date").order("id")
        .execute().data
    )


def _bank_item(
    tx: dict, *,
    attribution_month: str | None = None,
    settle_kind: str | None = None,
) -> dict:
    """銀行交易 → UI/合計用 item dict（amount 在 outflow 場景由呼叫端轉正）"""
    return {
        "transaction_date": tx.get("transaction_date"),
        "summary": tx.get("summary") or "",
        "counterparty": tx.get("counterparty") or "",
        "amount": tx.get("amount") or 0,
        "note": tx.get("note") or "",
        "channel": tx.get("channel") or "",
        "settle_kind": settle_kind,
        "attribution_month": attribution_month,
    }


# ============================================================================
# 澤沛 — 全記
# ============================================================================


def calculate_zepei_monthly(sb, service_month: str, clinic_id: int) -> ZepeiMonthly:
    m = ZepeiMonthly(service_month=service_month)
    next_month = _next_month(service_month)
    prev_m = _prev_month(service_month)

    # ─── 玉山健保戶：每筆都記 ───
    esun_id = _get_bank_account_id(sb, clinic_id, "健保戶")
    if esun_id:
        for tx in _fetch_bank_transactions(sb, esun_id, service_month):
            amt = tx["amount"]
            item = _bank_item(tx, attribution_month=service_month)
            if amt > 0:
                m.esun_inflow_items.append(item)
            elif amt < 0:
                item["amount"] = -amt
                m.esun_outflow_items.append(item)

    # ─── 中信進出戶：每筆都記，出帳時標註 settle_kind ───
    ctbc_id = _get_bank_account_id(sb, clinic_id, "進出戶")
    if ctbc_id:
        for tx in _fetch_bank_transactions(sb, ctbc_id, service_month):
            amt = tx["amount"]
            note = tx.get("note") or ""
            kind = _zepei_settle_kind(note) if amt < 0 else None
            attr = prev_m if kind else service_month
            item = _bank_item(tx, attribution_month=attr, settle_kind=kind)
            if amt > 0:
                m.ctbc_inflow_items.append(item)
            elif amt < 0:
                item["amount"] = -amt
                m.ctbc_outflow_items.append(item)

    # ─── 手 KEY x10 ───
    me_in = (
        sb.table("manual_entry")
        .select("entry_date, description, amount")
        .eq("clinic_id", clinic_id).eq("direction", "income")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    for r in me_in:
        m.x10_income_items.append({
            "entry_date": r.get("entry_date"),
            "description": r.get("description") or "",
            "amount": r.get("amount") or 0,
            "attribution_month": service_month,
        })
    me_ex = (
        sb.table("manual_entry")
        .select("entry_date, description, amount")
        .eq("clinic_id", clinic_id).eq("direction", "expense")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    for r in me_ex:
        m.x10_expense_items.append({
            "entry_date": r.get("entry_date"),
            "description": r.get("description") or "",
            "amount": r.get("amount") or 0,
            "attribution_month": service_month,
        })

    return m


# ============================================================================
# 澤豐 — 玉山逐筆（排除）+ 中信只抓 x6/x8 + 隱形 x3/x9/x12/x13 + 手 KEY x10
# ============================================================================


def calculate_zefeng_monthly(
    sb, service_month: str, clinic_id: int, zepei_clinic_id: int,
) -> ZefengMonthly:
    m = ZefengMonthly(service_month=service_month)
    next_month = _next_month(service_month)
    prev_m = _prev_month(service_month)

    # ─── 玉山健保戶：逐筆，排除 周院長個人 / 澤豐自家中信 ───
    esun_id = _get_bank_account_id(sb, clinic_id, "健保戶")
    if esun_id:
        for tx in _fetch_bank_transactions(sb, esun_id, service_month):
            amt = tx["amount"]
            cp = tx.get("counterparty") or ""
            if _is_zhou_personal_transfer(cp) or _is_zefeng_ctbc_internal(cp):
                continue
            item = _bank_item(tx, attribution_month=service_month)
            if amt > 0:
                m.esun_inflow_items.append(item)
            elif amt < 0:
                item["amount"] = -amt
                m.esun_outflow_items.append(item)

    # ─── 中信進出戶（混戶）：只抓 x6 / x8 ───
    ctbc_id = _get_bank_account_id(sb, clinic_id, "進出戶")
    if ctbc_id:
        # 月初餘額（前月最後 balance；資訊用）
        first = (
            sb.table("bank_transactions").select("balance, transaction_date")
            .eq("account_id", ctbc_id)
            .lt("transaction_date", service_month)
            .order("transaction_date", desc=True).order("id", desc=True)
            .limit(1).execute().data
        )
        if first:
            m.x1_prev_balance = first[0].get("balance") or 0

        # 預先抓 manual_annotation：判斷哪些「存現」是診所收入（x8）
        # 規則：scope=診所 + form=存現 + account=澤豐&個人中信 + clinic=澤豐
        # 比對 (entry_date, amount) 與當月中信入帳；description 含「11502」抽歸屬月
        ann_cash = (
            sb.table("manual_annotation")
            .select("entry_date, amount, description")
            .eq("scope", "診所").eq("form", "存現")
            .eq("account", "澤豐&個人中信").eq("clinic_id", clinic_id)
            .gte("entry_date", service_month).lt("entry_date", next_month)
            .execute().data
        )
        ann_x8_map: dict = {
            (r["entry_date"], int(r["amount"] or 0)): r for r in ann_cash
        }

        for tx in _fetch_bank_transactions(sb, ctbc_id, service_month):
            amt = tx["amount"]
            if amt <= 0:
                continue
            note = tx.get("note") or ""
            channel = tx.get("channel") or ""
            summary = tx.get("summary") or ""

            # x6 / x5 / x7 — 用 note 標籤辨識（澤沛主動標記，足以唯一）
            note_n = _normalize(note)
            kind = _zepei_settle_kind(note_n)
            if kind == "x6_fengpei":
                m.x6_fengpei_settle += amt
                m.x6_items.append(_bank_item(
                    tx, attribution_month=prev_m, settle_kind=kind,
                ))
                continue
            if kind in ("x5_cash", "x7_contract"):
                # 屬周院長個人，澤豐不記（即使物理上入帳）
                continue

            # x8 澤豐現金入帳：必須有 manual_annotation 對應才認列
            # （澤豐&個人中信戶混了個人存款，無法靠摘要判斷主體）
            if "現金" in summary or "存款機" in channel:
                key = (tx.get("transaction_date"), int(amt))
                ann = ann_x8_map.get(key)
                if ann is None:
                    continue  # 無註記 = 視為個人存款，不認列
                attr = _extract_attr_month_from_desc(
                    ann.get("description") or "", fallback=prev_m,
                )
                m.x8_zefeng_cash_revenue += amt
                m.x8_items.append(_bank_item(
                    tx, attribution_month=attr,
                ))
            # 其他入帳（玉山轉入、個人款項等）一律不記

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
    # x3 澤豐現金支出（排除支票，由 cash_expense 表負責）
    cash_zf = (
        sb.table("cash_expense")
        .select("expense_date, description, amount, accrual_month")
        .eq("clinic_id", clinic_id).eq("accrual_month", service_month)
        .execute().data
    )
    for r in cash_zf:
        amt = r.get("amount") or 0
        m.x3_zefeng_cash_expense += amt
        m.x3_items.append({
            "expense_date": r.get("expense_date"),
            "description": r.get("description") or "",
            "amount": amt,
            "attribution_month": service_month,
        })

    # x12 澤豐合約支出
    contract_zf = (
        sb.table("contract_expense")
        .select("service_month, vendor, amount, note")
        .eq("clinic_id", clinic_id).eq("service_month", service_month)
        .execute().data
    )
    for r in contract_zf:
        # int(round(...)) 防 excel 浮點誤差（73963.0 可能被存成 73962.99...）
        amt = int(round(float(r.get("amount") or 0)))
        m.x12_zefeng_contract_expense += amt
        m.x12_items.append({
            "service_month": r.get("service_month"),
            "vendor": r.get("vendor") or "",
            "note": r.get("note") or "",
            "amount": amt,
            "attribution_month": service_month,
        })

    # x9 編制外人力（謝松坊）
    # service_month = 11503 → 11503 實帳支付的薪水 = 11502 服務月薪資
    offsite_prev = (
        sb.table("staff_salary_summary")
        .select("employee_label, gross_salary, service_month")
        .eq("clinic_id", clinic_id).eq("service_month", prev_m)
        .execute().data
    )
    for r in offsite_prev:
        if "謝松坊" in (r.get("employee_label") or ""):
            amt = r.get("gross_salary") or 0
            m.x9_offsite_staff_pay += amt
            m.x9_items.append({
                "employee_label": r.get("employee_label"),
                "amount": amt,
                "service_month": r.get("service_month"),
                "attribution_month": prev_m,
            })

    # x13 周明毅院長薪資（兩院總和）
    # service_month = 11503 → 11503 實帳支付的薪水 = 11502 服務月薪資
    zhou_resp = sb.table("doctors").select("id").eq("name", "周明毅").execute().data
    if zhou_resp:
        zhou_id = zhou_resp[0]["id"]
        zhou_sal = (
            sb.table("doctor_salary_monthly")
            .select("total_salary, service_month")
            .eq("doctor_id", zhou_id)
            .eq("service_month", prev_m)
            .execute().data
        )
        for r in zhou_sal:
            amt = r.get("total_salary") or 0
            m.x13_zhou_doctor_salary += amt
            m.x13_items.append({
                "amount": amt,
                "service_month": r.get("service_month"),
                "attribution_month": prev_m,
            })

    # ─── 手 KEY x10 ───
    me_in = (
        sb.table("manual_entry")
        .select("entry_date, description, amount")
        .eq("clinic_id", clinic_id).eq("direction", "income")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    for r in me_in:
        m.x10_income_items.append({
            "entry_date": r.get("entry_date"),
            "description": r.get("description") or "",
            "amount": r.get("amount") or 0,
            "attribution_month": service_month,
        })
    me_ex = (
        sb.table("manual_entry")
        .select("entry_date, description, amount")
        .eq("clinic_id", clinic_id).eq("direction", "expense")
        .gte("entry_date", service_month).lt("entry_date", next_month)
        .execute().data
    )
    for r in me_ex:
        m.x10_expense_items.append({
            "entry_date": r.get("entry_date"),
            "description": r.get("description") or "",
            "amount": r.get("amount") or 0,
            "attribution_month": service_month,
        })

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
    """掃 bank_transactions 找有資料的月份"""
    months: set[str] = set()
    try:
        rows = sb.table("bank_transactions").select("transaction_date").execute().data
        for r in rows:
            d = r.get("transaction_date")
            if d:
                months.add(d[:7] + "-01")
    except Exception:
        pass
    return sorted(months, reverse=True)
