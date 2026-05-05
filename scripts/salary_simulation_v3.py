"""
醫師薪資試算 v3（115/01-115/03）

v3 修正（2026-05-01 院長補充）：
  1. 自費統計「其它」(C16) 欄抽成 50%（v2 漏掉，院長補正）
  2. 加入勞健保扣除額（手動配置，未來改成可編輯）
     - 支援醫師在支援診所：勞保 0、健保 0
     - 主聘診所：勞保預留欄（目前都 0）、健保依投保級距：
         澤豐 周明毅：60,800 → 扣 943
         澤豐 呂敏盛：45,800 → 扣 710
         澤沛 胡舒婷：45,800 → 扣 710
  3. 報表新增「應付月薪 / 勞保扣 / 健保扣 / 實領」欄

繼承 v2：
  - 抽成只從醫師自費統計總計列計算
  - 健保收入不抽成（只貢獻：診薪×診數、業績獎金）
  - 1-3月不含 A91+複針

輸出：scripts/salary_report_115Q1_v3.md
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


# ============================================================================
# 路徑與常數
# ============================================================================

FZ_NHI_ROOT = Path(
    r"C:\Users\User\Dropbox\家庭資料室\澤豐診所行政表單(櫃台電腦)\暫存\澤豐健保統計"
)
FP_NHI_ROOT = Path(
    r"C:\Users\User\Dropbox\家庭資料室\澤沛診所行政表單(櫃台電腦)\暫存\澤沛健保統計"
)
FZ_CASH_ROOT = Path(
    r"C:\Users\User\Dropbox\家庭資料室\澤豐診所行政表單(櫃台電腦)\行政&工作事項\醫師自費統計"
)
FP_CASH_ROOT = Path(
    r"C:\Users\User\Dropbox\家庭資料室\澤沛診所行政表單(櫃台電腦)\行政&工作事項\醫師自費統計"
)

OUTPUT_FILE = Path(__file__).parent / "salary_report_115Q1_v3.md"


# ─── 醫師基本資料 ─────────────────────────────────────────────
DOCTORS = {
    "周明毅": {"session_fee": 3231, "main_clinic": "澤豐", "is_director_in": "澤豐"},
    "呂敏盛": {"session_fee": 3167, "main_clinic": "澤豐", "is_director_in": None},
    "胡舒婷": {"session_fee": 3231, "main_clinic": "澤沛", "is_director_in": "澤沛"},
}

DIRECTOR_ALLOWANCE = 40000


# ─── 勞健保扣除額（依主聘診所×醫師）──────────────────────────
# 規則：只在主聘診所扣一次；支援診所為 0
# 健保扣除額 = 投保級距對應的勞工負擔健保費（手動配置，異動時修改此處）
INSURANCE_DEDUCTIONS = {
    # (主聘診所, 醫師): {"投保額": ..., "勞保扣": ..., "健保扣": ...}
    ("澤豐", "周明毅"): {"投保額": 60800, "勞保扣": 0, "健保扣": 943},
    ("澤豐", "呂敏盛"): {"投保額": 45800, "勞保扣": 0, "健保扣": 710},
    ("澤沛", "胡舒婷"): {"投保額": 45800, "勞保扣": 0, "健保扣": 710},
}


# ─── 自費統計欄位 ───────────────────────────────────────────
CASH_COLS = {
    "日期": 0, "病歷號": 1, "姓名": 2, "病名": 3, "用藥": 4, "醫師": 5,
    "掛號費": 6, "內服藥": 7, "外用藥": 8, "針灸費": 9, "傷科費": 10,
    "脫臼費": 11, "保養費": 12, "飲片費": 13, "診察費": 14, "檢驗費": 15,
    "其它": 16, "自費合計": 17,
}


# ─── 抽成率（v3：「其它」加入 50%）──────────────────────────
COMMISSION_RATES = {
    "掛號費": 0.0,
    "內服藥": 0.20, "外用藥": 0.20, "保養費": 0.20, "飲片費": 0.20,
    "針灸費": 0.40, "傷科費": 0.40, "脫臼費": 0.40,
    "檢驗費": 0.10,
    "其它": 0.50,        # v3 新增（院長補正）
}
DOCTOR_CONSULT_RATE = {
    "周明毅": 0.50,
    "呂敏盛": 0.0,
    "胡舒婷": 0.0,
}


# ─── 業績獎金 ─────────────────────────────────────────────
PERF_TRIGGER_AVG = 15.1
PERF_INTERNAL_BASE = 7
PERF_INTERNAL_RATE = 150
PERF_PURE_BASE = 6
PERF_PURE_RATE = 80
PERF_COMBO_BASE = 2
PERF_COMBO_RATE = 110

VISIT_COLS = {
    "醫師姓名": 1, "內科": 2, "純針": 3, "純傷": 4,
    "內+針": 5, "內+傷": 6, "健保總數": 7,
    "自費內科": 8, "自費針傷": 9, "合計": 10, "診數合計": 16,
}


# ============================================================================
# 工具
# ============================================================================


def _to_int(v) -> int:
    if pd.isna(v):
        return 0
    if isinstance(v, str):
        s = v.replace(",", "").strip()
        if not s or s in ("-", "—", "不計"):
            return 0
        try:
            return int(float(s))
        except ValueError:
            return 0
    return int(v)


# ============================================================================
# Parser: 醫師自費統計
# ============================================================================


@dataclass
class CashTotals:
    clinic: str
    doctor: str
    yyyymm: str
    registration: int = 0
    internal_drug: int = 0
    external_drug: int = 0
    acupuncture: int = 0
    trauma: int = 0
    dislocation: int = 0
    maintenance: int = 0
    decoction: int = 0
    consult: int = 0
    lab: int = 0
    other: int = 0          # v3: 加入計算
    total: int = 0
    source_file: str = ""


def parse_cash_report(file_path: Path, clinic: str, doctor: str, yyyymm: str) -> CashTotals:
    df = pd.read_excel(file_path, sheet_name=0, header=None)
    total_row_idx = None
    for r in range(df.shape[0]):
        v = df.iloc[r, 0]
        if pd.notna(v) and "總計" in str(v):
            total_row_idx = r
            break
    if total_row_idx is None:
        raise ValueError(f"{file_path.name}: 找不到「總計」列")
    row = df.iloc[total_row_idx]
    return CashTotals(
        clinic=clinic, doctor=doctor, yyyymm=yyyymm,
        registration=_to_int(row[CASH_COLS["掛號費"]]),
        internal_drug=_to_int(row[CASH_COLS["內服藥"]]),
        external_drug=_to_int(row[CASH_COLS["外用藥"]]),
        acupuncture=_to_int(row[CASH_COLS["針灸費"]]),
        trauma=_to_int(row[CASH_COLS["傷科費"]]),
        dislocation=_to_int(row[CASH_COLS["脫臼費"]]),
        maintenance=_to_int(row[CASH_COLS["保養費"]]),
        decoction=_to_int(row[CASH_COLS["飲片費"]]),
        consult=_to_int(row[CASH_COLS["診察費"]]),
        lab=_to_int(row[CASH_COLS["檢驗費"]]),
        other=_to_int(row[CASH_COLS["其它"]]),
        total=_to_int(row[CASH_COLS["自費合計"]]),
        source_file=file_path.name,
    )


def cash_path(clinic: str, doctor: str, yyyymm: str) -> Path | None:
    if clinic == "澤豐":
        d = FZ_CASH_ROOT / yyyymm
        candidates = [
            d / f"澤豐{doctor}醫師自費統計{yyyymm}.xlsx",
            d / f"{doctor}醫師自費統計{yyyymm}.xlsx",
        ]
    else:
        d = FP_CASH_ROOT / yyyymm
        short = {"周明毅": "周", "胡舒婷": "胡", "呂敏盛": "呂"}[doctor]
        candidates = [
            d / f"{yyyymm}月自費-{short}.xlsx",
            d / f"{yyyymm}自費-{short}.xlsx",
            d / f"澤沛{doctor}醫師自費統計{yyyymm}.xlsx",
            d / f"{yyyymm}{short}醫師自費統計.xlsx",
        ]
    for c in candidates:
        if c.exists():
            return c
    return None


# ============================================================================
# Parser: 健保人數+初診統計
# ============================================================================


def parse_visit_count(file_path: Path, clinic: str) -> pd.DataFrame:
    df = pd.read_excel(file_path, sheet_name=0, header=None)
    rows = []
    for r in range(5, df.shape[0]):
        name = df.iloc[r, VISIT_COLS["醫師姓名"]]
        if pd.isna(name):
            continue
        if str(name).strip() in ("合計：", "總計"):
            continue
        rec = {"clinic": clinic, "醫師": str(name).strip()}
        for k, c in VISIT_COLS.items():
            if k == "醫師姓名":
                continue
            rec[k] = _to_int(df.iloc[r, c])
        rows.append(rec)
    return pd.DataFrame(rows)


# ============================================================================
# 薪資計算
# ============================================================================


@dataclass
class SalaryComponent:
    clinic: str
    doctor: str
    yyyymm: str
    director_allowance: int = 0
    sessions: int = 0
    session_pay: int = 0
    cash_commission: int = 0
    cash_commission_detail: dict = field(default_factory=dict)
    perf_internal: int = 0
    perf_pure: int = 0
    perf_combo: int = 0
    perf_triggered: bool = False
    perf_avg_visits: float = 0.0
    visit_count_nhi: int = 0
    cash_data_missing: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def gross(self) -> int:
        """應付月薪（單一診所×醫師×月份，扣除前）"""
        return (
            self.director_allowance
            + self.session_pay
            + self.cash_commission
            + self.perf_internal
            + self.perf_pure
            + self.perf_combo
        )


def calc_cash_commission(ct: CashTotals) -> tuple[int, dict[str, int]]:
    detail: dict[str, int] = {}
    items = [
        ("內服藥", ct.internal_drug, COMMISSION_RATES["內服藥"]),
        ("外用藥", ct.external_drug, COMMISSION_RATES["外用藥"]),
        ("保養費", ct.maintenance, COMMISSION_RATES["保養費"]),
        ("飲片費", ct.decoction, COMMISSION_RATES["飲片費"]),
        ("針灸費", ct.acupuncture, COMMISSION_RATES["針灸費"]),
        ("傷科費", ct.trauma, COMMISSION_RATES["傷科費"]),
        ("脫臼費", ct.dislocation, COMMISSION_RATES["脫臼費"]),
        ("檢驗費", ct.lab, COMMISSION_RATES["檢驗費"]),
        ("其它", ct.other, COMMISSION_RATES["其它"]),
        ("診察費", ct.consult, DOCTOR_CONSULT_RATE.get(ct.doctor, 0.0)),
    ]
    total = 0
    for name, amt, rate in items:
        v = round(amt * rate)
        detail[name] = v
        total += v
    return total, detail


def calculate_component(
    *,
    clinic: str,
    doctor: str,
    yyyymm: str,
    visit_row: pd.Series,
    cash: CashTotals | None,
) -> SalaryComponent:
    info = DOCTORS[doctor]
    sc = SalaryComponent(clinic=clinic, doctor=doctor, yyyymm=yyyymm)

    if info["is_director_in"] == clinic:
        sc.director_allowance = DIRECTOR_ALLOWANCE

    sessions = _to_int(visit_row.get("診數合計", 0))
    sc.sessions = sessions
    sc.session_pay = sessions * info["session_fee"]

    if cash is None:
        sc.cash_data_missing = True
    else:
        total, detail = calc_cash_commission(cash)
        sc.cash_commission = total
        sc.cash_commission_detail = detail

    nhi_total_visits = _to_int(visit_row.get("健保總數", 0))
    sc.visit_count_nhi = nhi_total_visits
    if sessions > 0:
        avg = nhi_total_visits / sessions
        sc.perf_avg_visits = round(avg, 2)
        if avg >= PERF_TRIGGER_AVG:
            sc.perf_triggered = True
            v_internal = _to_int(visit_row.get("內科", 0))
            v_pure_zhen = _to_int(visit_row.get("純針", 0))
            v_pure_shang = _to_int(visit_row.get("純傷", 0))
            v_combo_zhen = _to_int(visit_row.get("內+針", 0))
            v_combo_shang = _to_int(visit_row.get("內+傷", 0))
            sc.perf_internal = max(0, v_internal - sessions * PERF_INTERNAL_BASE) * PERF_INTERNAL_RATE
            sc.perf_pure = max(0, (v_pure_zhen + v_pure_shang) - sessions * PERF_PURE_BASE) * PERF_PURE_RATE
            sc.perf_combo = max(0, (v_combo_zhen + v_combo_shang) - sessions * PERF_COMBO_BASE) * PERF_COMBO_RATE

    return sc


# ============================================================================
# 主流程
# ============================================================================


def load_visits(yyyymm: str) -> pd.DataFrame:
    fz = parse_visit_count(
        FZ_NHI_ROOT / "澤豐健保人數&初診統計" / f"{yyyymm}澤豐健保人數&初診統計.xlsx",
        "澤豐",
    )
    fp = parse_visit_count(
        FP_NHI_ROOT / "澤沛健保人數&初診統計" / f"{yyyymm}澤沛健保人數&初診統計.xlsx",
        "澤沛",
    )
    return pd.concat([fz, fp], ignore_index=True)


def run_simulation(months: list[str]) -> tuple[list[SalaryComponent], list[CashTotals]]:
    components: list[SalaryComponent] = []
    cash_totals: list[CashTotals] = []
    for ym in months:
        visits = load_visits(ym)
        vis_idx = visits.set_index(["clinic", "醫師"])
        for clinic in ("澤豐", "澤沛"):
            for doctor in DOCTORS:
                key = (clinic, doctor)
                if key not in vis_idx.index:
                    continue
                visit_row = vis_idx.loc[key]
                cp = cash_path(clinic, doctor, ym)
                cash = None
                if cp:
                    try:
                        cash = parse_cash_report(cp, clinic, doctor, ym)
                        cash_totals.append(cash)
                    except Exception as e:
                        print(f"WARN: {cp.name}: {e}")
                sc = calculate_component(
                    clinic=clinic, doctor=doctor, yyyymm=ym,
                    visit_row=visit_row, cash=cash,
                )
                components.append(sc)
    return components, cash_totals


# ============================================================================
# 彙總（含勞健保扣除）
# ============================================================================


@dataclass
class MonthlyPayslip:
    """單月份單醫師（彙總跨支援）的薪資單"""
    yyyymm: str
    doctor: str
    main_clinic: str
    gross_main: int = 0          # 主聘診所小計
    gross_support: int = 0       # 支援診所小計（豐沛金流墊付項）
    support_clinic: str | None = None
    labor_deduction: int = 0
    nhi_deduction: int = 0
    insurance_base: int = 0      # 健保投保額（顯示用）

    @property
    def gross_total(self) -> int:
        return self.gross_main + self.gross_support

    @property
    def take_home(self) -> int:
        return self.gross_total - self.labor_deduction - self.nhi_deduction


def build_payslips(comps: list[SalaryComponent]) -> list[MonthlyPayslip]:
    out: list[MonthlyPayslip] = []
    # group by (yyyymm, doctor)
    groups: dict[tuple[str, str], list[SalaryComponent]] = {}
    for c in comps:
        groups.setdefault((c.yyyymm, c.doctor), []).append(c)

    for (ym, doc), cs in sorted(groups.items()):
        info = DOCTORS[doc]
        main_clinic = info["main_clinic"]
        ps = MonthlyPayslip(yyyymm=ym, doctor=doc, main_clinic=main_clinic)
        for c in cs:
            if c.clinic == main_clinic:
                ps.gross_main += c.gross
            else:
                ps.gross_support += c.gross
                ps.support_clinic = c.clinic

        ded = INSURANCE_DEDUCTIONS.get((main_clinic, doc), {})
        ps.labor_deduction = ded.get("勞保扣", 0)
        ps.nhi_deduction = ded.get("健保扣", 0)
        ps.insurance_base = ded.get("投保額", 0)
        out.append(ps)
    return out


# ============================================================================
# 報表輸出
# ============================================================================


def render_md(comps: list[SalaryComponent], cash: list[CashTotals],
              payslips: list[MonthlyPayslip]) -> str:
    L: list[str] = []
    L.append("# 醫師薪資試算 v3（115年 1-3月）")
    L.append("")
    L.append("> ⚠️ **試算性質，不入庫**。1-3月不含 A91+複針 獎金。")
    L.append("> v3：自費「其它」抽 50%、加入勞健保扣除額（手動配置）。")
    L.append("")
    L.append("## 試算規則")
    L.append("")
    L.append("**月薪（應付） = 院長津貼 + 診薪×診數 + 自費抽成 + 業績獎金**")
    L.append("**實領 = 月薪（應付）− 勞保扣除 − 健保扣除**")
    L.append("")
    L.append("### 自費抽成比例")
    L.append("")
    L.append("| 項目 | 周明毅 | 其他醫師 |")
    L.append("|---|:---:|:---:|")
    L.append("| 掛號費 | 0% | 0% |")
    L.append("| 內服藥 / 外用藥 / 保養費 / 飲片費 | 20% | 20% |")
    L.append("| 針灸費 / 傷科費 / 脫臼費 | 40% | 40% |")
    L.append("| 診察費 | **50%** | **0%** |")
    L.append("| 檢驗費 | 10% | 10% |")
    L.append("| **其它（v3 新增）** | **50%** | **50%** |")
    L.append("")
    L.append("### 業績獎金（健保每診平均 ≥ 15.1 觸發）")
    L.append("")
    L.append("- 內科業績 = max(0, 內科健保人次 − 診數×7) × 150")
    L.append("- 純針純傷 = max(0, (純針+純傷)健保人次 − 診數×6) × 80")
    L.append("- 內+組合 = max(0, (內+針+內+傷)健保人次 − 診數×2) × 110")
    L.append("")
    L.append("### 院長津貼 / 診薪")
    L.append("")
    L.append(f"- 主聘院長：月領 NT$ {DIRECTOR_ALLOWANCE:,}")
    L.append("- 診薪×診數：周明毅 3,231 / 呂敏盛 3,167 / 胡舒婷 3,231")
    L.append("")
    L.append("### 勞健保扣除額（手動配置，異動時修改 `INSURANCE_DEDUCTIONS`）")
    L.append("")
    L.append("> 規則：只在主聘診所扣一次；支援診所扣除額為 0")
    L.append("> 目前所有醫師都未加入勞保（勞保扣 = 0）；健保依投保級距")
    L.append("")
    L.append("| 主聘診所 | 醫師 | 投保額 | 勞保扣 | 健保扣 |")
    L.append("|---|---|---:|---:|---:|")
    for (mc, doc), v in INSURANCE_DEDUCTIONS.items():
        L.append(
            f"| {mc} | {doc} | {v['投保額']:,} | {v['勞保扣']:,} | {v['健保扣']:,} |"
        )
    L.append("")
    L.append("---")

    months = sorted({c.yyyymm for c in comps})
    for ym in months:
        roc_y, roc_m = int(ym[:3]), int(ym[3:])
        ad_y = roc_y + 1911
        L.append(f"\n## {ym}（西元 {ad_y}-{roc_m:02d}）")
        L.append("")

        # 自費統計總計
        L.append("### 自費統計總計")
        L.append("")
        L.append(
            "| 診所 | 醫師 | 內服 | 外用 | 針灸 | 傷科 | 脫臼 | 保養 | 飲片 | 診察 | 檢驗 | 其它 | 自費合計 |"
        )
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        month_cash = [c for c in cash if c.yyyymm == ym]
        for c in sorted(month_cash, key=lambda x: (x.doctor, x.clinic)):
            L.append(
                f"| {c.clinic} | {c.doctor} | {c.internal_drug:,} | {c.external_drug:,} | "
                f"{c.acupuncture:,} | {c.trauma:,} | {c.dislocation:,} | {c.maintenance:,} | "
                f"{c.decoction:,} | {c.consult:,} | {c.lab:,} | {c.other:,} | **{c.total:,}** |"
            )
        missing = [c for c in comps if c.yyyymm == ym and c.cash_data_missing]
        if missing:
            L.append("")
            L.append("> ⚠️ **以下醫師當月自費統計檔缺失，抽成計入 0：**")
            for c in missing:
                L.append(f"> - {c.clinic} {c.doctor}")
        L.append("")

        # 分診所薪資明細
        L.append("### 分診所薪資明細（應付）")
        L.append("")
        L.append(
            "| 診所 | 醫師 | 院長津貼 | 診數 | 診薪×診數 | 自費抽成 | 業績獎金 | 平均人次 | 觸發 | 應付 | 備註 |"
        )
        L.append(
            "|---|---|---:|---:|---:|---:|---:|---:|:---:|---:|:---|"
        )
        month_comps = [c for c in comps if c.yyyymm == ym]
        for c in sorted(month_comps, key=lambda x: (x.doctor, x.clinic)):
            perf_total = c.perf_internal + c.perf_pure + c.perf_combo
            note = "⚠️ 自費資料缺" if c.cash_data_missing else ""
            L.append(
                f"| {c.clinic} | {c.doctor} | {c.director_allowance:,} | {c.sessions} | "
                f"{c.session_pay:,} | {c.cash_commission:,} | {perf_total:,} | "
                f"{c.perf_avg_visits} | {'✅' if c.perf_triggered else '—'} | "
                f"**{c.gross:,}** | {note} |"
            )
        L.append("")

        # 月薪實領（含扣除）
        L.append("### 醫師月薪結構（應付 → 扣除 → 實領）")
        L.append("")
        L.append(
            "| 主聘 | 醫師 | 主聘診所應付 | 支援診所應付 | 應付合計 | 勞保扣 | 健保扣 | **實領** |"
        )
        L.append("|---|---|---:|---:|---:|---:|---:|---:|")
        month_ps = [p for p in payslips if p.yyyymm == ym]
        for p in sorted(month_ps, key=lambda x: (x.main_clinic, x.doctor)):
            L.append(
                f"| {p.main_clinic} | {p.doctor} | {p.gross_main:,} | "
                f"{p.gross_support:,} | {p.gross_total:,} | "
                f"{p.labor_deduction:,} | {p.nhi_deduction:,} | "
                f"**{p.take_home:,}** |"
            )
        L.append("")

        # 跨支援墊付
        L.append("### 跨支援墊付（豐沛金流項目）")
        L.append("")
        cross = [(p.main_clinic, p.support_clinic, p.doctor, p.gross_support)
                 for p in month_ps if p.gross_support > 0 and p.support_clinic]
        if not cross:
            L.append("（無）")
        else:
            L.append("| 墊付方（主聘） | 應由（看診診所）還 | 醫師 | 金額 |")
            L.append("|---|---|---|---:|")
            for main, vis, doc, amt in sorted(cross):
                L.append(f"| {main} | {vis} | {doc} | {amt:,} |")
        L.append("")
        L.append("---")

    # 季彙總（含實領）
    L.append("\n## 1-3月 季彙總")
    L.append("")
    L.append("| 主聘 | 醫師 | 應付合計 | 勞保扣 | 健保扣 | 實領 | 備註 |")
    L.append("|---|---|---:|---:|---:|---:|:---|")
    qg: dict[tuple[str, str], dict[str, int]] = {}
    for p in payslips:
        k = (p.main_clinic, p.doctor)
        qg.setdefault(k, {"gross": 0, "lab": 0, "nhi": 0, "take": 0})
        qg[k]["gross"] += p.gross_total
        qg[k]["lab"] += p.labor_deduction
        qg[k]["nhi"] += p.nhi_deduction
        qg[k]["take"] += p.take_home
    for (mc, doc), v in sorted(qg.items()):
        miss = sorted({c.yyyymm for c in comps
                       if c.doctor == doc and c.cash_data_missing})
        note = f"⚠️ 缺自費 {','.join(miss)}" if miss else ""
        L.append(
            f"| {mc} | {doc} | {v['gross']:,} | {v['lab']:,} | "
            f"{v['nhi']:,} | **{v['take']:,}** | {note} |"
        )
    L.append("")

    # v2 → v3 差異
    L.append("\n## v3 vs v2 差異")
    L.append("")
    L.append("- **「其它」(C16) 抽 50%**：v2 漏算（院長補正）")
    L.append("- **新增勞健保扣除**：v2 沒扣，現在實領 = 應付 − 勞保 − 健保")
    L.append("")
    L.append("**11503 周明毅 澤豐 自費抽成 v3 驗算（看 v2 是否需修正）：**")
    L.append("")
    # 找出周明毅澤豐 11503
    for c in cash:
        if c.yyyymm == "11503" and c.clinic == "澤豐" and c.doctor == "周明毅":
            total, detail = calc_cash_commission(c)
            for k, v in detail.items():
                amt = getattr(c, {
                    "內服藥": "internal_drug", "外用藥": "external_drug",
                    "保養費": "maintenance", "飲片費": "decoction",
                    "針灸費": "acupuncture", "傷科費": "trauma",
                    "脫臼費": "dislocation", "檢驗費": "lab",
                    "其它": "other", "診察費": "consult",
                }[k])
                rate = v / amt if amt else 0
                L.append(f"- {k} {amt:,} × {rate:.0%} = {v:,}")
            L.append(f"- **合計 = {total:,}**")
            break
    L.append("")

    L.append("\n## 已釐清")
    L.append("")
    L.append("✅ 抽成只看自費統計，健保不抽")
    L.append("✅ 「其它」C16 抽 50%（v3 已修正）")
    L.append("✅ 業績獎金人次取自健保人數+初診統計")
    L.append("✅ 11503 數字接近院長手算 — 方向正確")
    L.append("")
    L.append("## 待補/未來事項")
    L.append("")
    L.append("- [ ] 院長從診所醫療資訊系統下載 11501/11502 澤豐自費統計檔，補進 Dropbox 後重跑")
    L.append("- [ ] 勞健保扣除額未來需可在系統 UI 編輯（目前寫死於腳本 `INSURANCE_DEDUCTIONS`）")
    L.append("- [ ] 4月薪資需加入 A91+複針 獎金（115/04 新制起算）")
    L.append("- [ ] 數字小差異待正式入系統時逐一比對校正")
    return "\n".join(L)


def main() -> int:
    months = ["11501", "11502", "11503"]
    comps, cash = run_simulation(months)
    payslips = build_payslips(comps)
    print(f"comps={len(comps)} cash={len(cash)} payslips={len(payslips)}")
    md = render_md(comps, cash, payslips)
    OUTPUT_FILE.write_text(md, encoding="utf-8")
    print(f"v3 報告: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
