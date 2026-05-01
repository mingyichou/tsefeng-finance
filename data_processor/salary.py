"""
醫師薪資計算引擎（Phase 3.5）

從 DB 讀資料、計算月度薪資、寫入 doctor_salary_monthly。

資料來源：
  - doctors                          → session_fee 診薪
  - doctor_clinic                    → role + director_allowance
  - doctor_visit_stats               → 診數 + 各類健保人次（業績獎金）
  - doctor_cash_monthly (view)       → 自費月度總計（抽成基底）
  - doctor_commission_rules          → 抽成率（預設）
  - doctor_commission_overrides      → 醫師個別覆寫（如周醫師診察費 50%）
  - doctor_insurance_deductions      → 勞健保扣除額
  - bonus_rules                      → 業績獎金門檻 15.1（讀取但邏輯硬寫）

公式：
  應付 = director_allowance + session_pay + commission_total + bonus_total
       (+ acu_complex_bonus + a91_bonus 從 4月起 — Sprint 2.4 完成才有)
  實領 = 應付 − labor_deduction − nhi_deduction
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


# ─── 業績獎金固定常數 ────────────────────────────────────
PERF_INTERNAL_BASE = 7
PERF_INTERNAL_RATE = 150
PERF_PURE_BASE = 6
PERF_PURE_RATE = 80
PERF_COMBO_BASE = 2
PERF_COMBO_RATE = 110
PERF_TRIGGER_AVG_FALLBACK = 15.1   # 若 bonus_rules 表查不到時的備援


# ─── 抽成欄位對應 ────────────────────────────────────────
# DB 欄位名 → doctor_cash_monthly view 欄位名
COMMISSION_FIELDS = [
    "registration", "internal_drug", "external_drug",
    "acupuncture", "trauma", "dislocation",
    "wellness", "herb_decoction", "consult", "lab", "other",
]


@dataclass
class SalaryComponent:
    """單一(診所×醫師×月份)的薪資組成"""
    clinic_id: int
    clinic_name: str
    doctor_id: int
    doctor_name: str
    service_month: str   # 'YYYY-MM-01'
    role: str            # 'director' / 'regular' / 'support'
    director_allowance: int = 0
    sessions_total: int = 0
    session_pay: int = 0
    commission_total: int = 0
    commission_breakdown: dict[str, int] = field(default_factory=dict)
    avg_visits_per_session: float = 0.0
    bonus_internal: int = 0
    bonus_pure_acu_trauma: int = 0
    bonus_internal_combo: int = 0
    bonus_total: int = 0
    perf_triggered: bool = False
    visit_count_nhi: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def is_main_clinic(self) -> bool:
        return self.role != "support"

    @property
    def gross(self) -> int:
        return (
            self.director_allowance
            + self.session_pay
            + self.commission_total
            + self.bonus_total
        )

    def to_db_row(self) -> dict:
        """回傳可寫入 doctor_salary_monthly 的 dict（單列）"""
        return {
            "clinic_id": self.clinic_id,
            "doctor_id": self.doctor_id,
            "service_month": self.service_month,
            "director_allowance": self.director_allowance,
            "session_count": self.sessions_total,
            "session_pay": self.session_pay,
            "commission_total": self.commission_total,
            "commission_breakdown": self.commission_breakdown,
            "avg_visits_per_session": self.avg_visits_per_session,
            "bonus_internal": self.bonus_internal,
            "bonus_pure_acu_trauma": self.bonus_pure_acu_trauma,
            "bonus_internal_combo": self.bonus_internal_combo,
            "bonus_total": self.bonus_total,
            "labor_deduction": 0,    # 此列扣除 — 主聘列才填，非主聘 0
            "nhi_deduction": 0,
        }


@dataclass
class Payslip:
    """單醫師單月份的薪資單（彙總跨支援）"""
    yyyymm: str
    doctor_id: int
    doctor_name: str
    main_clinic_id: int
    main_clinic_name: str
    gross_main: int = 0
    gross_support: int = 0
    support_clinic_id: int | None = None
    support_clinic_name: str | None = None
    labor_deduction: int = 0
    nhi_deduction: int = 0
    insurance_base: int = 0

    @property
    def gross_total(self) -> int:
        return self.gross_main + self.gross_support

    @property
    def take_home(self) -> int:
        return self.gross_total - self.labor_deduction - self.nhi_deduction


# ============================================================================
# 從 DB 載入資料
# ============================================================================


def fetch_salary_inputs(sb, service_month: str) -> dict:
    """
    一次性從 DB 拉本次計算需要的所有資料。

    Args:
        sb: Supabase client
        service_month: 'YYYY-MM-01'

    Returns:
        dict 含：
          doctors, clinics, doctor_clinic, visit_stats, cash_monthly,
          commission_rules, commission_overrides, insurance_deductions,
          bonus_threshold
    """
    doctors = sb.table("doctors").select("id, name, session_fee, is_active").execute().data
    clinics = sb.table("clinics").select("id, short_name").execute().data

    doctor_clinic = sb.table("doctor_clinic").select(
        "doctor_id, clinic_id, role, director_allowance"
    ).execute().data

    visit_stats = (
        sb.table("doctor_visit_stats")
        .select("*")
        .eq("service_month", service_month)
        .execute().data
    )
    cash_monthly = (
        sb.table("doctor_cash_monthly")
        .select("*")
        .eq("service_month", service_month)
        .execute().data
    )

    commission_rules = sb.table("doctor_commission_rules").select(
        "item_field, item_label, default_rate"
    ).execute().data
    commission_overrides = sb.table("doctor_commission_overrides").select(
        "doctor_id, item_field, rate"
    ).execute().data

    insurance_deductions = sb.table("doctor_insurance_deductions").select(
        "clinic_id, doctor_id, insurance_base, labor_deduction, nhi_deduction, "
        "effective_from, effective_to"
    ).execute().data

    bonus_rules = sb.table("bonus_rules").select(
        "rule_name, threshold_avg"
    ).execute().data
    bonus_threshold = next(
        (float(r["threshold_avg"]) for r in bonus_rules
         if r["rule_name"] == "high_volume_bonus"),
        PERF_TRIGGER_AVG_FALLBACK,
    )

    return {
        "doctors": {d["id"]: d for d in doctors},
        "clinics": {c["id"]: c for c in clinics},
        "doctor_clinic": doctor_clinic,
        "visit_stats": {
            (v["clinic_id"], v["doctor_id"]): v for v in visit_stats
        },
        "cash_monthly": {
            (v["clinic_id"], v["doctor_id"]): v for v in cash_monthly
        },
        "commission_rules": {r["item_field"]: float(r["default_rate"]) for r in commission_rules},
        "commission_overrides": {
            (o["doctor_id"], o["item_field"]): float(o["rate"])
            for o in commission_overrides
        },
        "insurance_deductions": insurance_deductions,
        "bonus_threshold": bonus_threshold,
    }


# ============================================================================
# 計算
# ============================================================================


def _resolve_rate(
    inputs: dict, doctor_id: int, item_field: str
) -> float:
    """醫師個別 override 優先；否則用 rules default。"""
    ov = inputs["commission_overrides"].get((doctor_id, item_field))
    if ov is not None:
        return ov
    return inputs["commission_rules"].get(item_field, 0.0)


def _calc_commission(
    inputs: dict, doctor_id: int, cash_row: dict | None
) -> tuple[int, dict[str, int]]:
    """以該醫師對應的抽成率 × 自費月度總計各項目"""
    breakdown: dict[str, int] = {}
    if not cash_row:
        return 0, {f: 0 for f in COMMISSION_FIELDS}
    total = 0
    for f in COMMISSION_FIELDS:
        amt = cash_row.get(f) or 0
        rate = _resolve_rate(inputs, doctor_id, f)
        v = round(amt * rate)
        breakdown[f] = v
        total += v
    return total, breakdown


def _calc_perf_bonus(
    visit_row: dict | None, sessions: int, threshold: float
) -> tuple[bool, float, int, int, int]:
    """業績獎金（健保每診平均 ≥ threshold 觸發）"""
    if not visit_row or sessions <= 0:
        return False, 0.0, 0, 0, 0
    nhi_total = visit_row.get("nhi_visits_total") or 0
    avg = nhi_total / sessions
    if avg < threshold:
        return False, round(avg, 2), 0, 0, 0
    v_internal = visit_row.get("nhi_internal") or 0
    v_pure_acu = visit_row.get("nhi_pure_acu") or 0
    v_pure_trauma = visit_row.get("nhi_pure_trauma") or 0
    v_int_acu = visit_row.get("nhi_internal_acu") or 0
    v_int_trauma = visit_row.get("nhi_internal_trauma") or 0

    b_internal = max(0, v_internal - sessions * PERF_INTERNAL_BASE) * PERF_INTERNAL_RATE
    b_pure = max(0, (v_pure_acu + v_pure_trauma) - sessions * PERF_PURE_BASE) * PERF_PURE_RATE
    b_combo = max(0, (v_int_acu + v_int_trauma) - sessions * PERF_COMBO_BASE) * PERF_COMBO_RATE
    return True, round(avg, 2), b_internal, b_pure, b_combo


def calculate_components(inputs: dict, service_month: str) -> list[SalaryComponent]:
    """
    為該月份所有 (診所×醫師) 組合計算 SalaryComponent。
    依 doctor_clinic 表的所有列為計算基礎，不限於有 visit_stats 的。
    （沒看診診數=0、抽成=0、業績不觸發；但記錄一筆便於審計）
    """
    components: list[SalaryComponent] = []
    threshold = inputs["bonus_threshold"]

    for dc in inputs["doctor_clinic"]:
        doctor_id = dc["doctor_id"]
        clinic_id = dc["clinic_id"]
        role = dc["role"]
        director_allowance = dc["director_allowance"] or 0
        doctor = inputs["doctors"].get(doctor_id)
        clinic = inputs["clinics"].get(clinic_id)
        if not doctor or not clinic:
            continue
        if not doctor.get("is_active", True):
            continue

        visit_row = inputs["visit_stats"].get((clinic_id, doctor_id))
        cash_row = inputs["cash_monthly"].get((clinic_id, doctor_id))

        sessions = (visit_row or {}).get("sessions_total", 0) or 0
        # session_fee 為 NUMERIC(7,1)（如 3230.8），先乘診數最後再 round
        session_fee = float(doctor.get("session_fee") or 0)
        session_pay = round(sessions * session_fee)
        commission_total, breakdown = _calc_commission(inputs, doctor_id, cash_row)
        triggered, avg, b_int, b_pure, b_combo = _calc_perf_bonus(
            visit_row, sessions, threshold
        )

        sc = SalaryComponent(
            clinic_id=clinic_id,
            clinic_name=clinic["short_name"],
            doctor_id=doctor_id,
            doctor_name=doctor["name"],
            service_month=service_month,
            role=role,
            director_allowance=director_allowance,
            sessions_total=sessions,
            session_pay=session_pay,
            commission_total=commission_total,
            commission_breakdown=breakdown,
            avg_visits_per_session=avg,
            bonus_internal=b_int,
            bonus_pure_acu_trauma=b_pure,
            bonus_internal_combo=b_combo,
            bonus_total=b_int + b_pure + b_combo,
            perf_triggered=triggered,
            visit_count_nhi=(visit_row or {}).get("nhi_visits_total", 0) or 0,
        )

        if not visit_row:
            sc.notes.append("無看診人數資料（doctor_visit_stats 缺）")
        if not cash_row:
            sc.notes.append("無自費資料（doctor_cash_monthly 缺）")

        components.append(sc)

    return components


def build_payslips(
    components: list[SalaryComponent],
    inputs: dict,
    service_month: str,
) -> list[Payslip]:
    """彙總到主聘診所，加上勞健保扣除"""
    # 按 doctor_id group
    by_doctor: dict[int, list[SalaryComponent]] = {}
    for c in components:
        by_doctor.setdefault(c.doctor_id, []).append(c)

    # 找每位醫師的主聘 (role != 'support' 的列)；若全是 support 則取第一個
    sm_dt = date.fromisoformat(service_month)

    payslips: list[Payslip] = []
    for doctor_id, comps in by_doctor.items():
        main = next((c for c in comps if c.role != "support"), comps[0])
        support = next((c for c in comps if c.role == "support" and c.gross > 0), None)

        ps = Payslip(
            yyyymm=service_month,
            doctor_id=doctor_id,
            doctor_name=main.doctor_name,
            main_clinic_id=main.clinic_id,
            main_clinic_name=main.clinic_name,
            gross_main=main.gross,
            gross_support=support.gross if support else 0,
            support_clinic_id=support.clinic_id if support else None,
            support_clinic_name=support.clinic_name if support else None,
        )

        # 勞健保扣除（主聘診所、生效期間有效的）
        for ins in inputs["insurance_deductions"]:
            if ins["clinic_id"] != main.clinic_id:
                continue
            if ins["doctor_id"] != doctor_id:
                continue
            ef_from = date.fromisoformat(ins["effective_from"])
            ef_to = (
                date.fromisoformat(ins["effective_to"])
                if ins.get("effective_to") else None
            )
            if ef_from > sm_dt:
                continue
            if ef_to and ef_to < sm_dt:
                continue
            ps.labor_deduction = ins.get("labor_deduction") or 0
            ps.nhi_deduction = ins.get("nhi_deduction") or 0
            ps.insurance_base = ins.get("insurance_base") or 0
            break

        payslips.append(ps)

    return payslips


# ============================================================================
# 寫入 doctor_salary_monthly
# ============================================================================


def upsert_salary_monthly(
    sb,
    components: list[SalaryComponent],
    payslips: list[Payslip],
):
    """
    寫入 doctor_salary_monthly。每個 (clinic_id, doctor_id, service_month) 一列。
    扣除額只填在主聘診所那列，支援診所那列扣除為 0。
    """
    # 索引：哪些 (clinic, doctor) 是主聘
    main_set = {(p.main_clinic_id, p.doctor_id): p for p in payslips}

    rows = []
    for c in components:
        row = c.to_db_row()
        ps = main_set.get((c.clinic_id, c.doctor_id))
        if ps:  # 此列為主聘
            row["labor_deduction"] = ps.labor_deduction
            row["nhi_deduction"] = ps.nhi_deduction
        rows.append(row)

    if not rows:
        return 0
    resp = (
        sb.table("doctor_salary_monthly")
        .upsert(rows, on_conflict="clinic_id,doctor_id,service_month")
        .execute()
    )
    return len(resp.data) if resp.data else len(rows)


# ============================================================================
# 高層 API：一次跑完
# ============================================================================


def run_salary_calculation(
    sb,
    service_month: str,
) -> tuple[list[SalaryComponent], list[Payslip]]:
    """讀資料 → 計算 → 回傳 (components, payslips)。不寫入 DB。"""
    inputs = fetch_salary_inputs(sb, service_month)
    components = calculate_components(inputs, service_month)
    payslips = build_payslips(components, inputs, service_month)
    return components, payslips
