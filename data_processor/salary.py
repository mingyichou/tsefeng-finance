"""
醫師薪資計算引擎（Phase 3.5；v15 雙軌制）

從 DB 讀資料、計算月度薪資、寫入 doctor_salary_monthly。

資料來源：
  - doctors                          → 診薪 fallback、salary_scheme（legacy/dual）
  - doctor_session_fees              → 薪資參數（日期版本化，取該月月底有效值）：
                                       診薪 + 新制參數（執照費/開診費/健保%/銷售三段%/操作%）
  - doctor_clinic                    → role + director_allowance（日期版本化）
  - doctor_visit_stats               → 診數 + 各類健保人次（舊制業績獎金）
  - doctor_cash_monthly (view)       → 自費月度總計（抽成基底）
  - doctor_outpatient_summary        → 複針/A91 人數；健保總額/藥費（新制健保業績獎金）
  - doctor_commission_rules          → 抽成率（預設 20/40/10/50、診察費 0）
  - doctor_commission_overrides      → 醫師個別覆寫（如周醫師診察費 50%）
  - doctor_insurance_deductions      → 勞健保扣除額
  - bonus_rules                      → 業績獎金門檻 15.1

舊制（legacy；周明毅/呂敏盛/胡舒婷）：
  應付 = director_allowance + 診薪×診數 + 自費抽成 + 業績獎金(每診平均≥15.1) + 複針 + A91

新制雙軌（dual；115/10 起入職，院長 2026-09-13 裁定）：哪個高領哪個
  保障薪 = 診薪×診數 + 自費獎金(固定 20%/40%/10%/50%) + A91 獎金 + 複針獎金
  業績薪 = 執照費(基數×該院週診數) + 開診費×診數 + 健保業績獎金 + 自費獎金(業績版)
    健保業績獎金 = (健保總額 − 藥費) × 健保抽成比例（預設 34%）
    自費獎金(業績版) = 銷售獎金(累進：≤10萬 20%、10~15萬 23%、>15萬 25%)
                     + 操作獎金(針灸+傷科+脫臼 × 40%) + 三伏貼(檢驗費×10%) + 診斷書(其它×50%)
  A91/複針已含在健保點數內 → 業績薪不另計；舊制的「每診平均≥15.1 業績獎金」與院長津貼
  皆不在新制公式內。**兩間診所獨立計算**（院長 2026-09-13）：每院各自比較保障薪 vs 業績薪，
  可一院領保障薪、另一院領業績薪；銷售累進以該院銷售額計算；執照費各院 = 執照費基數 × 該院週診數
  （如基數 1250、澤豐 6 診／澤沛 2 診 → 澤豐 7,500、澤沛 2,500）。

  實領 = 應付 − 勞保扣 − 健保扣（兩制相同）

v16（院長 2026-11-06）：
  - 國定假日薪資：每醫師獨立設定 16 個節日的每日薪資（doctor_holiday_pay），
    算薪時把落在該月、金額 > 0 的節日列出加總，計入**主聘診所列**的應付
    （舊制：應付 ＋ 國定假日薪資；新制：採用軌道小計 ＋ 國定假日薪資，不影響保障/業績比較）。
    全部為 0（未約定／該月無節日）→ 薪資單不出現此區塊。日期規則見 holidays.py。
  - 銷售獎金算法二選一（doctor_session_fees.sales_tier_mode）：
    progressive 累進級距（原設計）／ whole 全額級距（達到級距後全銷售額同一比例）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .doctor_config import (
    DEFAULT_SALARY_PARAMS,
    SALES_TIER_MODE_LABEL,
    WEEKLY_SESSIONS_FIELD,
    active_dc_rows,
    fetch_doctor_clinic,
    fetch_session_fees,
    insurance_for_month,
    license_fee_for_clinic,
    normalize_sales_tier_mode,
    parse_date,
    resolve_salary_params,
)
from .holidays import (
    fetch_holiday_overrides,
    fetch_holiday_pay,
    holiday_pay_for_month,
)


# ─── 業績獎金固定常數（舊制）────────────────────────────
PERF_INTERNAL_BASE = 7
PERF_INTERNAL_RATE = 150
PERF_PURE_BASE = 6
PERF_PURE_RATE = 80
PERF_COMBO_BASE = 2
PERF_COMBO_RATE = 110
PERF_TRIGGER_AVG_FALLBACK = 15.1   # 若 bonus_rules 表查不到時的備援


# ─── A91+複針 獎金（115/04 新制起算）────────────────────
ACU_COMPLEX_MID_RATE = 20    # 中度複針 × 20 元/人
ACU_COMPLEX_HIGH_RATE = 40   # 高度複針 × 40 元/人
A91_RATE = 14                # A91 整合醫療 × 14 元/人
ACU_A91_EFFECTIVE_FROM = "2026-04-01"   # 4月起套用


# ─── 雙軌制（115/10 起新進醫師）────────────────────────
DUAL_SCHEME_FROM = "2026-10-01"      # 到職日 ≥ 此日期 → 新增醫師預設新制
SALES_TIER_1_MAX = 100_000           # 銷售累進：≤ 10 萬
SALES_TIER_2_MAX = 150_000           #           10 萬 ~ 15 萬；> 15 萬第三段
PERF_LAB_RATE = 0.10                 # 三伏(九)貼 = 檢驗費 × 10%（固定）
PERF_OTHER_RATE = 0.50               # 診斷書 = 其它 × 50%（固定）
SALES_FIELDS = ("internal_drug", "external_drug", "wellness", "herb_decoction")
OPERATION_FIELDS = ("acupuncture", "trauma", "dislocation")
TRACK_LABEL = {"guaranteed": "保障薪", "performance": "業績薪"}


# ─── 抽成欄位對應 ────────────────────────────────────────
# DB 欄位名 → doctor_cash_monthly view 欄位名
COMMISSION_FIELDS = [
    "registration", "internal_drug", "external_drug",
    "acupuncture", "trauma", "dislocation",
    "wellness", "herb_decoction", "consult", "lab", "other",
]

# doctor_salary_monthly v15 新增欄位（migration 未跑時寫入要剔除）
DUAL_DB_FIELDS = (
    "salary_scheme", "chosen_track", "license_fee", "opening_pay",
    "nhi_points_ex_drug", "nhi_perf_bonus", "perf_commission_total",
    "perf_commission_breakdown", "guaranteed_total", "performance_total",
)
# doctor_salary_monthly v16 新增欄位（國定假日薪資）
HOLIDAY_DB_FIELDS = ("holiday_pay", "holiday_breakdown")
# 寫入時的可選欄位群：(欄位, 缺欄時的 migration 提示, 「本批資料需要此群」判斷)
_OPTIONAL_DB_FIELD_GROUPS = (
    (DUAL_DB_FIELDS, "migration_v14_to_v15.sql（雙軌制欄位）",
     lambda comps: any(c.is_dual for c in comps)),
    (HOLIDAY_DB_FIELDS, "migration_v15_to_v16.sql（國定假日薪資欄位）",
     lambda comps: any(c.holiday_pay for c in comps)),
)


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
    # 各類人次（顯示用，業績獎金母數）
    visit_internal: int = 0
    visit_pure_acu: int = 0
    visit_pure_trauma: int = 0
    visit_internal_acu: int = 0
    visit_internal_trauma: int = 0
    # 4月起新制
    acu_complex_mid_count: int = 0
    acu_complex_high_count: int = 0
    acu_complex_bonus: int = 0
    a91_count: int = 0
    a91_bonus: int = 0
    # 角色列生效區間（主聘判定用，不寫入 DB）
    role_from: str | None = None
    role_to: str | None = None
    notes: list[str] = field(default_factory=list)

    # ── v15 雙軌制（salary_scheme='dual'）──
    salary_scheme: str = "legacy"
    chosen_track: str | None = None            # 'guaranteed' / 'performance'
    salary_params: dict = field(default_factory=dict)   # 該月適用參數（顯示用）
    license_fee: int = 0                       # 執照費 = 基數 × 該院週診數
    opening_pay: int = 0                       # 開診費 × 診數
    nhi_points_ex_drug: int = 0                # 健保總額 − 藥費
    nhi_perf_bonus: int = 0                    # 健保業績獎金
    perf_sales_revenue: int = 0                # 銷售額（內服+外用+保養+飲片）
    perf_sales_bonus: int = 0                  # 銷售獎金（累進；該院銷售額）
    perf_sales_tiers: list[dict] = field(default_factory=list)  # 累進各段（該院）
    perf_operation_revenue: int = 0            # 針灸+傷科+脫臼
    perf_operation_bonus: int = 0
    perf_lab_bonus: int = 0                    # 三伏(九)貼
    perf_other_bonus: int = 0                  # 診斷書

    # ── v16 國定假日薪資（只填在主聘診所列；兩制皆加在應付上）──
    holiday_pay: int = 0
    holiday_items: list[dict] = field(default_factory=list)   # [{key,label,date,amount}]

    @property
    def is_main_clinic(self) -> bool:
        return self.role != "support"

    @property
    def is_dual(self) -> bool:
        return self.salary_scheme == "dual"

    @property
    def perf_commission_total(self) -> int:
        return (self.perf_sales_bonus + self.perf_operation_bonus
                + self.perf_lab_bonus + self.perf_other_bonus)

    @property
    def perf_commission_breakdown(self) -> dict[str, int]:
        return {
            "sales": self.perf_sales_bonus,
            "operation": self.perf_operation_bonus,
            "lab": self.perf_lab_bonus,
            "other": self.perf_other_bonus,
        }

    @property
    def guaranteed_total(self) -> int:
        """新制保障薪（此診所列）= 診薪×診數 + 自費獎金(固定%) + 複針 + A91"""
        return (self.session_pay + self.commission_total
                + self.acu_complex_bonus + self.a91_bonus)

    @property
    def performance_total(self) -> int:
        """新制業績薪（此診所列）= 執照費(基數×該院週診數) + 開診費×診數 + 健保業績獎金 + 自費獎金(業績版)"""
        return (self.license_fee + self.opening_pay
                + self.nhi_perf_bonus + self.perf_commission_total)

    @property
    def gross_before_holiday(self) -> int:
        """不含國定假日薪資的應付（新制＝採用軌道小計；舊制＝原公式）"""
        if self.is_dual:
            if self.chosen_track == "performance":
                return self.performance_total
            return self.guaranteed_total
        return (
            self.director_allowance
            + self.session_pay
            + self.commission_total
            + self.bonus_total
            + self.acu_complex_bonus
            + self.a91_bonus
        )

    @property
    def gross(self) -> int:
        """此診所列應付 ＝ 制度應付 ＋ 國定假日薪資（v16；非主聘列 holiday_pay 恆為 0）"""
        return self.gross_before_holiday + self.holiday_pay

    def to_db_row(self) -> dict:
        """回傳可寫入 doctor_salary_monthly 的 dict（單列）"""
        return {
            "clinic_id": self.clinic_id,
            "doctor_id": self.doctor_id,
            "service_month": self.service_month,
            "director_allowance": 0 if self.is_dual else self.director_allowance,
            "session_count": self.sessions_total,
            "session_pay": self.session_pay,
            "commission_total": self.commission_total,
            "commission_breakdown": self.commission_breakdown,
            "avg_visits_per_session": self.avg_visits_per_session,
            "bonus_internal": self.bonus_internal,
            "bonus_pure_acu_trauma": self.bonus_pure_acu_trauma,
            "bonus_internal_combo": self.bonus_internal_combo,
            "bonus_total": self.bonus_total,
            "acu_complex_mid_count": self.acu_complex_mid_count,
            "acu_complex_high_count": self.acu_complex_high_count,
            "acu_complex_bonus": self.acu_complex_bonus,
            "a91_count": self.a91_count,
            "a91_bonus": self.a91_bonus,
            "labor_deduction": 0,    # 主聘列才填，非主聘 0
            "nhi_deduction": 0,
            # v15
            "salary_scheme": self.salary_scheme,
            "chosen_track": self.chosen_track if self.is_dual else None,
            "license_fee": self.license_fee,
            "opening_pay": self.opening_pay,
            "nhi_points_ex_drug": self.nhi_points_ex_drug,
            "nhi_perf_bonus": self.nhi_perf_bonus,
            "perf_commission_total": self.perf_commission_total,
            "perf_commission_breakdown": self.perf_commission_breakdown,
            "guaranteed_total": self.guaranteed_total if self.is_dual else 0,
            "performance_total": self.performance_total if self.is_dual else 0,
            # v16
            "holiday_pay": self.holiday_pay,
            "holiday_breakdown": list(self.holiday_items) if self.holiday_items else None,
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
    insurance_note: str | None = None
    # v15 雙軌（各院獨立比較；chosen_track = 主聘列採用軌道，track_by_clinic 逐院）
    salary_scheme: str = "legacy"
    chosen_track: str | None = None
    guaranteed_total: int = 0          # 兩院保障薪加總（顯示用）
    performance_total: int = 0         # 兩院業績薪加總（顯示用）
    track_by_clinic: dict = field(default_factory=dict)   # {clinic_name: track}
    # v16 國定假日薪資（已含在 gross_main 內；此處供顯示）
    holiday_pay: int = 0
    holiday_items: list[dict] = field(default_factory=list)

    @property
    def chosen_label(self) -> str:
        """採用薪別標籤：各院相同 → '保障薪'；不同 → '澤沛 保障薪／澤豐 業績薪'"""
        if not self.is_dual or not self.track_by_clinic:
            return "—"
        tracks = set(self.track_by_clinic.values())
        if len(tracks) == 1:
            return TRACK_LABEL.get(next(iter(tracks)), "?")
        return "／".join(f"{cn} {TRACK_LABEL.get(t, '?')}"
                         for cn, t in self.track_by_clinic.items())

    @property
    def gross_total(self) -> int:
        return self.gross_main + self.gross_support

    @property
    def take_home(self) -> int:
        return self.gross_total - self.labor_deduction - self.nhi_deduction

    @property
    def is_dual(self) -> bool:
        return self.salary_scheme == "dual"


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
          doctors, clinics, doctor_clinic, session_fees, visit_stats, cash_monthly,
          outpatient, commission_rules, commission_overrides, insurance_deductions,
          bonus_threshold
    """
    try:
        doctors = sb.table("doctors").select(
            "id, name, session_fee, is_active, salary_scheme"
        ).execute().data
    except Exception:  # v15 migration 未跑 → 全部視為舊制
        doctors = sb.table("doctors").select("id, name, session_fee, is_active").execute().data
    clinics = sb.table("clinics").select("id, short_name").execute().data

    doctor_clinic = fetch_doctor_clinic(sb)      # 含生效日期（migration 前自動 fallback）
    session_fees = fetch_session_fees(sb)        # 薪資參數歷史（表不存在回 []）

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
    outpatient = (
        sb.table("doctor_outpatient_summary")
        .select("clinic_id, doctor_id, service_month, "
                "acu_complex_mid_count, acu_complex_high_count, a91_count, "
                "nhi_total_points, nhi_drug_fee")
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

    # v16 國定假日給薪設定（表不存在 → {}）＋ 年度日期覆寫
    holiday_pay = fetch_holiday_pay(sb)
    holiday_overrides = fetch_holiday_overrides(sb)

    return {
        "doctors": {d["id"]: d for d in doctors},
        "clinics": {c["id"]: c for c in clinics},
        "doctor_clinic": doctor_clinic,
        "session_fees": session_fees,
        "visit_stats": {
            (v["clinic_id"], v["doctor_id"]): v for v in visit_stats
        },
        "cash_monthly": {
            (v["clinic_id"], v["doctor_id"]): v for v in cash_monthly
        },
        "outpatient": {
            (v["clinic_id"], v["doctor_id"]): v for v in outpatient
        },
        "commission_rules": {r["item_field"]: float(r["default_rate"]) for r in commission_rules},
        "commission_overrides": {
            (o["doctor_id"], o["item_field"]): float(o["rate"])
            for o in commission_overrides
        },
        "insurance_deductions": insurance_deductions,
        "bonus_threshold": bonus_threshold,
        "holiday_pay": holiday_pay,              # {doctor_id: {holiday_key: amount}}
        "holiday_overrides": holiday_overrides,  # {"2027": {key: "YYYY-MM-DD"}}
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
    """業績獎金（健保每診平均 ≥ threshold 觸發）— 舊制"""
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


# ─── 新制：業績薪自費獎金（銷售累進）──────────────────────


def progressive_sales_bonus(
    total_sales: int, rate_1: float, rate_2: float, rate_3: float,
) -> tuple[int, list[dict]]:
    """
    銷售獎金累進：≤100,000 × rate_1、100,001~150,000 × rate_2、150,001+ × rate_3
    （rate 為百分比數字，如 20 / 23 / 25）。例：160,000 → 100,000×20% + 50,000×23% + 10,000×25%。

    Returns (獎金合計(四捨五入), [{"label","base","rate","bonus"}...])
    """
    total_sales = max(int(total_sales or 0), 0)
    t1 = min(total_sales, SALES_TIER_1_MAX)
    t2 = min(max(total_sales - SALES_TIER_1_MAX, 0), SALES_TIER_2_MAX - SALES_TIER_1_MAX)
    t3 = max(total_sales - SALES_TIER_2_MAX, 0)
    tiers = [
        {"label": f"≤{SALES_TIER_1_MAX:,}", "base": t1, "rate": float(rate_1)},
        {"label": f"{SALES_TIER_1_MAX + 1:,}~{SALES_TIER_2_MAX:,}", "base": t2, "rate": float(rate_2)},
        {"label": f">{SALES_TIER_2_MAX:,}", "base": t3, "rate": float(rate_3)},
    ]
    raw_total = 0.0
    for t in tiers:
        raw = t["base"] * t["rate"] / 100
        t["bonus"] = round(raw)
        raw_total += raw
    return round(raw_total), tiers


def whole_sales_bonus(
    total_sales: int, rate_1: float, rate_2: float, rate_3: float,
) -> tuple[int, list[dict]]:
    """
    銷售獎金「全額級距」（v16 第二種算法，院長 2026-11-06）：
      銷售額 < 100,000           → 全額 × rate_1
      100,000 ≤ 銷售額 < 150,000 → 全額 × rate_2（含 10 萬以內部分）
      銷售額 ≥ 150,000           → 全額 × rate_3（含 15 萬以內部分）
    例：160,000 → 160,000 × 25% = 40,000（累進則為 20,000＋11,500＋2,500 = 34,000）。
    Returns (獎金, [單一級距 {"label","base","rate","bonus"}])，與 progressive_sales_bonus 同形。
    """
    total_sales = max(int(total_sales or 0), 0)
    if total_sales < SALES_TIER_1_MAX:
        label, rate = f"全額（<{SALES_TIER_1_MAX:,}）", float(rate_1)
    elif total_sales < SALES_TIER_2_MAX:
        label, rate = f"全額（{SALES_TIER_1_MAX:,}~<{SALES_TIER_2_MAX:,}）", float(rate_2)
    else:
        label, rate = f"全額（≥{SALES_TIER_2_MAX:,}）", float(rate_3)
    bonus = round(total_sales * rate / 100)
    return bonus, [{"label": label, "base": total_sales, "rate": rate, "bonus": bonus}]


def sales_bonus(total_sales: int, params: dict) -> tuple[int, list[dict]]:
    """依 params['sales_tier_mode'] 分派：progressive（預設）／whole。"""
    mode = normalize_sales_tier_mode((params or {}).get("sales_tier_mode"))
    r1 = (params or {}).get("sales_rate_1", 20)
    r2 = (params or {}).get("sales_rate_2", 23)
    r3 = (params or {}).get("sales_rate_3", 25)
    if mode == "whole":
        return whole_sales_bonus(total_sales, r1, r2, r3)
    return progressive_sales_bonus(total_sales, r1, r2, r3)


def sales_mode_label(params: dict | None) -> str:
    """薪資單／畫面用的短標籤：累進級距／全額級距"""
    mode = normalize_sales_tier_mode((params or {}).get("sales_tier_mode"))
    return SALES_TIER_MODE_LABEL.get(mode, mode).split("（")[0]


def _apply_dual_scheme(components: list[SalaryComponent], inputs: dict) -> None:
    """
    新制醫師：每個（診所×醫師）列**獨立**計算業績薪各項，並各自比較保障薪 vs 業績薪
    （院長 2026-09-13 裁定：兩間診所獨立計算，可一院領保障薪、另一院領業績薪）。
    保障薪各項（診薪×診數/自費固定%/複針/A91）已在 calculate_components 算好。
    執照費各院 = 執照費基數 × 該院週診數（license_fee_for_clinic）。
    """
    by_doc: dict[int, list[SalaryComponent]] = {}
    for c in components:
        if c.is_dual:
            by_doc.setdefault(c.doctor_id, []).append(c)

    for _did, comps in by_doc.items():
        params = comps[0].salary_params or dict(DEFAULT_SALARY_PARAMS)
        opening_fee = float(params.get("opening_fee") or 0)
        nhi_rate = float(params.get("nhi_rate") or 0)
        op_rate = float(params.get("operation_rate") or 0)

        for c in comps:
            cash = inputs["cash_monthly"].get((c.clinic_id, c.doctor_id)) or {}
            op = inputs["outpatient"].get((c.clinic_id, c.doctor_id)) or {}
            c.license_fee = license_fee_for_clinic(params, c.clinic_name)
            c.opening_pay = round(c.sessions_total * opening_fee)
            pts = int(op.get("nhi_total_points") or 0) - int(op.get("nhi_drug_fee") or 0)
            c.nhi_points_ex_drug = max(pts, 0)
            c.nhi_perf_bonus = round(c.nhi_points_ex_drug * nhi_rate / 100)
            c.perf_sales_revenue = sum(int(cash.get(f) or 0) for f in SALES_FIELDS)
            c.perf_sales_bonus, c.perf_sales_tiers = sales_bonus(
                c.perf_sales_revenue, params,
            )
            c.perf_operation_revenue = sum(int(cash.get(f) or 0) for f in OPERATION_FIELDS)
            c.perf_operation_bonus = round(c.perf_operation_revenue * op_rate / 100)
            c.perf_lab_bonus = round(int(cash.get("lab") or 0) * PERF_LAB_RATE)
            c.perf_other_bonus = round(int(cash.get("other") or 0) * PERF_OTHER_RATE)
            # 舊制業績獎金 / 院長津貼不在新制公式內
            c.bonus_internal = c.bonus_pure_acu_trauma = c.bonus_internal_combo = 0
            c.bonus_total = 0
            c.perf_triggered = False
            if c.director_allowance:
                c.notes.append(
                    f"院長津貼 {c.director_allowance:,} 不在新制公式內，未計入"
                )
            if not op:
                c.notes.append("無門診申報主表（健保業績獎金計 0）")
            # 該院獨立比較：哪個高領哪個（相等取保障薪）
            c.chosen_track = (
                "performance" if c.performance_total > c.guaranteed_total else "guaranteed"
            )


def _main_component(comps: list[SalaryComponent]) -> SalaryComponent:
    """該醫師該月的主聘列：role != 'support' 中生效最新的一列（月中轉診所取新診所）；
    全是支援列則取第一列。build_payslips 與國定假日薪資歸屬共用，確保落在同一列。"""
    mains = [c for c in comps if c.role != "support"]
    if mains:
        return max(mains, key=lambda c: parse_date(c.role_from) or date.min)
    return comps[0]


def _apply_holiday_pay(components: list[SalaryComponent], inputs: dict, service_month: str) -> None:
    """v16：每位醫師該月國定假日薪資（只列落在該月且每日薪資 > 0 的節日）放到主聘列。
    未設定／全 0／該月無節日 → holiday_pay=0、items=[]（薪資單不顯示區塊）。"""
    pay_cfg = inputs.get("holiday_pay") or {}
    overrides = inputs.get("holiday_overrides") or {}
    by_doc: dict[int, list[SalaryComponent]] = {}
    for c in components:
        c.holiday_pay, c.holiday_items = 0, []
        by_doc.setdefault(c.doctor_id, []).append(c)
    for did, comps in by_doc.items():
        amounts = pay_cfg.get(did)
        if not amounts:
            continue
        total, items = holiday_pay_for_month(amounts, service_month, overrides)
        if total <= 0:
            continue
        main = _main_component(comps)
        main.holiday_pay, main.holiday_items = total, items


def calculate_components(inputs: dict, service_month: str) -> list[SalaryComponent]:
    """
    為該月份所有 (診所×醫師) 組合計算 SalaryComponent。
    依 doctor_clinic 表「該月有效」的角色列為計算基礎（v12 起帶生效區間：
    到職前/離職後的月份不再列入），不限於有 visit_stats 的。
    （沒看診診數=0、抽成=0、業績不觸發；但記錄一筆便於審計）
    新制醫師（doctors.salary_scheme='dual'）另跑 _apply_dual_scheme。
    """
    components: list[SalaryComponent] = []
    threshold = inputs["bonus_threshold"]

    for dc in active_dc_rows(inputs["doctor_clinic"], service_month):
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
        outpatient_row = inputs["outpatient"].get((clinic_id, doctor_id))

        sessions = (visit_row or {}).get("sessions_total", 0) or 0
        # v12/v15：薪資參數取「該月月底有效」的版本（doctor_session_fees），
        # 無歷史列時 fallback doctors.session_fee + 預設新制參數
        params = resolve_salary_params(
            inputs.get("session_fees") or [], inputs["doctors"],
            doctor_id, service_month,
        )
        session_fee = float(params["session_fee"])
        session_pay = round(sessions * session_fee)
        commission_total, breakdown = _calc_commission(inputs, doctor_id, cash_row)
        triggered, avg, b_int, b_pure, b_combo = _calc_perf_bonus(
            visit_row, sessions, threshold
        )

        # 4月起新制：A91+複針獎金
        acu_mid = (outpatient_row or {}).get("acu_complex_mid_count", 0) or 0
        acu_high = (outpatient_row or {}).get("acu_complex_high_count", 0) or 0
        a91_cnt = (outpatient_row or {}).get("a91_count", 0) or 0
        if service_month >= ACU_A91_EFFECTIVE_FROM:
            acu_bonus = acu_mid * ACU_COMPLEX_MID_RATE + acu_high * ACU_COMPLEX_HIGH_RATE
            a91_b = a91_cnt * A91_RATE
        else:
            acu_bonus = 0
            a91_b = 0

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
            visit_internal=(visit_row or {}).get("nhi_internal", 0) or 0,
            visit_pure_acu=(visit_row or {}).get("nhi_pure_acu", 0) or 0,
            visit_pure_trauma=(visit_row or {}).get("nhi_pure_trauma", 0) or 0,
            visit_internal_acu=(visit_row or {}).get("nhi_internal_acu", 0) or 0,
            visit_internal_trauma=(visit_row or {}).get("nhi_internal_trauma", 0) or 0,
            acu_complex_mid_count=acu_mid,
            acu_complex_high_count=acu_high,
            acu_complex_bonus=acu_bonus,
            a91_count=a91_cnt,
            a91_bonus=a91_b,
            role_from=dc.get("effective_from"),
            role_to=dc.get("effective_to"),
            salary_scheme=(doctor.get("salary_scheme") or "legacy"),
            salary_params=params,
        )

        if not visit_row:
            sc.notes.append("無看診人數資料（doctor_visit_stats 缺）")
        if not cash_row:
            sc.notes.append("無自費資料（doctor_cash_monthly 缺）")

        components.append(sc)

    _apply_dual_scheme(components, inputs)
    _apply_holiday_pay(components, inputs, service_month)
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

    # 找每位醫師的主聘 (role != 'support' 的列)；若全是 support 則取第一個。
    # 月中轉診所（同月兩個主聘列）取生效較新的那家當主聘（扣除只扣一次）。
    payslips: list[Payslip] = []
    for doctor_id, comps in by_doctor.items():
        main = _main_component(comps)
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
            salary_scheme=main.salary_scheme,
            chosen_track=main.chosen_track if main.is_dual else None,
            guaranteed_total=sum(c.guaranteed_total for c in comps) if main.is_dual else 0,
            performance_total=sum(c.performance_total for c in comps) if main.is_dual else 0,
            track_by_clinic=(
                {c.clinic_name: c.chosen_track for c in comps} if main.is_dual else {}
            ),
            holiday_pay=main.holiday_pay,
            holiday_items=list(main.holiday_items),
        )

        # 勞健保扣除（主聘診所）：
        #   勞保按日比例（扣除額/30 × 當月在保天數，整月在保全額）
        #   健保當月任一天在保即扣全額
        ins = insurance_for_month(
            inputs["insurance_deductions"],
            main.clinic_id, doctor_id, service_month,
        )
        ps.labor_deduction = ins["labor"]
        ps.nhi_deduction = ins["nhi"]
        ps.insurance_base = ins["base"]
        ps.insurance_note = ins["note"]

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
    可選欄位群（v15 雙軌制／v16 國定假日）在 DB 不存在（migration 未跑）時：
    本批資料用不到該群（無新制醫師／無國定假日薪資）→ 剔除該群欄位重試；
    用得到 → 直接報錯並指出要跑哪支 migration（否則 total_salary 會算錯）。
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

    stripped: set[str] = set()
    for _attempt in range(len(_OPTIONAL_DB_FIELD_GROUPS) + 1):
        try:
            resp = (
                sb.table("doctor_salary_monthly")
                .upsert(rows, on_conflict="clinic_id,doctor_id,service_month")
                .execute()
            )
            return len(resp.data) if resp.data else len(rows)
        except Exception as e:
            msg = str(e)
            group = next(
                (g for g in _OPTIONAL_DB_FIELD_GROUPS
                 if any(f in msg for f in g[0]) and not set(g[0]) <= stripped),
                None,
            )
            if group is None:
                raise
            fields, migration_hint, needed = group
            if needed(components):
                raise ValueError(
                    f"資料庫尚未執行 {migration_hint}，本月資料需要這些欄位，無法寫入。"
                    "請先到 Supabase SQL Editor 執行 migration。"
                ) from e
            stripped.update(fields)
            rows = [{k: v for k, v in r.items() if k not in stripped} for r in rows]
    raise RuntimeError("doctor_salary_monthly 寫入失敗（欄位相容重試用盡）")


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
