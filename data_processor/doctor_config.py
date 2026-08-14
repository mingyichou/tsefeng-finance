"""
醫師配置日期版本化共用模組（v12）

醫師-診所角色（doctor_clinic）、診薪（doctor_session_fees）、
勞健保扣除額（doctor_insurance_deductions）自 v12 起皆帶
effective_from / effective_to 生效區間，本模組提供：

  - 月份界線與區間重疊判斷
  - doctor_clinic 依薪資月份過濾 + 同 (醫師,診所) 取最新生效列
  - 診薪解析：取「該月月底有效」的版本計整月
  - 勞健保扣除：
      勞保 = 按日比例（扣除額/30 × 當月在保天數；整月在保扣全額）
      健保 = 當月任一天在保即扣全額
  - migration 未跑時的 fallback（無日期欄位視為永久有效）

月份歸屬規則（與院長確認的語意）：
  - 角色列與該月有任何重疊 → 該月參與計算（診數本來就按實際看診）
  - 診薪/院長津貼採「該月最後一天有效」的版本計整月
    → 調參數請以月初（如 10/1）為生效日，才不會整月用新值
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta


# ─── 日期工具 ────────────────────────────────────────────────


def parse_date(v) -> date | None:
    """DB 回傳的 date 字串（或 None）轉 date。"""
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def month_bounds(service_month: str) -> tuple[date, date]:
    """'YYYY-MM-01' → (月初, 月底)"""
    start = date.fromisoformat(service_month)
    last = calendar.monthrange(start.year, start.month)[1]
    return start, date(start.year, start.month, last)


def row_overlaps_month(row: dict, m_start: date, m_end: date) -> bool:
    """生效區間與月份有任何重疊。無 effective_from 欄（未跑 migration）視為永久有效。"""
    ef_from = parse_date(row.get("effective_from"))
    ef_to = parse_date(row.get("effective_to"))
    if ef_from and ef_from > m_end:
        return False
    if ef_to and ef_to < m_start:
        return False
    return True


# ─── doctor_clinic 角色配置 ──────────────────────────────────


def active_dc_rows(dc_rows: list[dict], service_month: str) -> list[dict]:
    """
    過濾出該月有效的角色列；同 (doctor_id, clinic_id) 若有多列重疊
    （如月中調津貼），取 effective_from 最新的一列。
    """
    m_start, m_end = month_bounds(service_month)
    best: dict[tuple[int, int], dict] = {}
    for r in dc_rows:
        if not row_overlaps_month(r, m_start, m_end):
            continue
        key = (r["doctor_id"], r["clinic_id"])
        cur = best.get(key)
        if cur is None:
            best[key] = r
            continue
        new_from = parse_date(r.get("effective_from")) or date.min
        cur_from = parse_date(cur.get("effective_from")) or date.min
        if new_from > cur_from:
            best[key] = r
    return list(best.values())


def fetch_doctor_clinic(sb) -> list[dict]:
    """讀 doctor_clinic 含生效日期；migration 未跑時 fallback 舊欄位。"""
    try:
        return sb.table("doctor_clinic").select(
            "id, doctor_id, clinic_id, role, director_allowance, "
            "effective_from, effective_to, note"
        ).execute().data
    except Exception:
        return sb.table("doctor_clinic").select(
            "doctor_id, clinic_id, role, director_allowance"
        ).execute().data


# ─── 診薪版本 ────────────────────────────────────────────────


def fetch_session_fees(sb) -> list[dict]:
    """讀 doctor_session_fees；表不存在（migration 未跑）回 []。"""
    try:
        return sb.table("doctor_session_fees").select(
            "id, doctor_id, session_fee, effective_from, effective_to, note"
        ).execute().data
    except Exception:
        return []


def resolve_session_fee(
    fee_rows: list[dict],
    doctors: dict[int, dict],
    doctor_id: int,
    service_month: str,
) -> float:
    """
    該醫師該月適用診薪 = 歷史列中 effective_from ≤ 月底、取最新的一筆。
    無任何歷史列（migration 未跑 / 未搬資料）→ fallback doctors.session_fee。
    """
    _, m_end = month_bounds(service_month)
    best_row = None
    best_from = None
    for r in fee_rows:
        if r["doctor_id"] != doctor_id:
            continue
        ef_from = parse_date(r.get("effective_from")) or date.min
        if ef_from > m_end:
            continue
        if best_from is None or ef_from > best_from:
            best_from, best_row = ef_from, r
    if best_row is not None:
        return float(best_row.get("session_fee") or 0)
    doc = doctors.get(doctor_id) or {}
    return float(doc.get("session_fee") or 0)


# ─── 勞健保扣除（按日比例）──────────────────────────────────


def insurance_for_month(
    ins_rows: list[dict],
    clinic_id: int,
    doctor_id: int,
    service_month: str,
) -> dict:
    """
    該 (主聘診所, 醫師, 月份) 的勞健保扣除。

    勞保：依天數比例 = round(扣除額 / 30 × 當月在保天數)；
          整月在保扣全額；多段（月中調投保額）各段分別比例後加總。
    健保：當月任一天在保即扣全額（取生效最新的一筆）。

    Returns:
        {"labor": int, "nhi": int, "base": int, "note": str | None}
    """
    m_start, m_end = month_bounds(service_month)
    days_in_month = (m_end - m_start).days + 1

    overlapping = [
        r for r in ins_rows
        if r["clinic_id"] == clinic_id and r["doctor_id"] == doctor_id
        and row_overlaps_month(r, m_start, m_end)
    ]
    if not overlapping:
        return {"labor": 0, "nhi": 0, "base": 0, "note": None}

    # 健保 / 投保額：取生效最新的一筆，全額
    newest = max(
        overlapping,
        key=lambda r: parse_date(r.get("effective_from")) or date.min,
    )
    nhi = int(newest.get("nhi_deduction") or 0)
    base = int(newest.get("insurance_base") or 0)

    # 勞保：逐段按日比例
    labor_total = 0
    notes: list[str] = []
    for r in overlapping:
        amount = int(r.get("labor_deduction") or 0)
        if amount <= 0:
            continue
        ef_from = parse_date(r.get("effective_from")) or date.min
        ef_to = parse_date(r.get("effective_to"))
        seg_start = max(ef_from, m_start)
        seg_end = min(ef_to, m_end) if ef_to else m_end
        covered_days = (seg_end - seg_start).days + 1
        if seg_start <= m_start and seg_end >= m_end:
            labor_total += amount            # 整月在保 → 全額
        else:
            covered_days = min(covered_days, 30)
            prorated = round(amount / 30 * covered_days)
            labor_total += prorated
            notes.append(
                f"勞保按日比例 {covered_days}/30 天"
                f"（{amount:,} → {prorated:,}）"
            )

    # 健保若非整月在保，仍扣全額（規則：當月在保即全額），註記提示
    newest_from = parse_date(newest.get("effective_from")) or date.min
    newest_to = parse_date(newest.get("effective_to"))
    if nhi > 0 and (
        newest_from > m_start or (newest_to and newest_to < m_end)
    ):
        notes.append("健保當月在保即扣全額")

    _ = days_in_month  # 保留：目前勞保比例固定以 30 天為分母

    return {
        "labor": labor_total,
        "nhi": nhi,
        "base": base,
        "note": "；".join(notes) if notes else None,
    }


# ─── 在職狀態判斷（系統設定 UI 用）──────────────────────────


def doctor_employment_status(dc_rows: list[dict], today: date | None = None) -> dict[int, dict]:
    """
    依 doctor_clinic 全部列彙整每位醫師在職狀態。

    Returns:
        {doctor_id: {
            "active": bool,          # 今日（或未來）仍有有效角色列
            "last_end": date | None, # 全部角色都結束時的最後結束日
        }}
    """
    today = today or date.today()
    out: dict[int, dict] = {}
    by_doctor: dict[int, list[dict]] = {}
    for r in dc_rows:
        by_doctor.setdefault(r["doctor_id"], []).append(r)
    for did, rows in by_doctor.items():
        active = False
        ends: list[date] = []
        for r in rows:
            ef_to = parse_date(r.get("effective_to"))
            if ef_to is None or ef_to >= today:
                active = True
            else:
                ends.append(ef_to)
        out[did] = {
            "active": active,
            "last_end": max(ends) if (ends and not active) else None,
        }
    return out


def previous_day(d: date) -> date:
    return d - timedelta(days=1)
