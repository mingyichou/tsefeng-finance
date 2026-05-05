"""
醫師薪資試算 v2（115/01-115/03）— 抽成規則修正

⚠️ 試算性質 — 不入庫，產出 markdown 報告給院長對帳
⚠️ 1-3月 不含 A91+複針 獎金（115/04 新制起算前）

v2 修正（2026-05-01 院長澄清）：
  - 抽成完全來自「醫師自費統計」檔的「總計」列，不再使用門診申報的自費欄位
  - 抽成項目（10 類）：
      掛號費 0% / 內服藥 20% / 外用藥 20% / 針灸費 40% / 傷科費 40%
      脫臼費 40% / 保養費 20% / 飲片費 20% / 診察費 (周明毅 50%/ 其他 0%) / 檢驗費 10%
  - 健保收入不抽成（健保部分只貢獻：診薪×診數、業績獎金）
  - 澤豐 11501/11502 自費統計檔缺 → 抽成記為 0 並標示「資料缺」

資料來源（Dropbox）：
  - 業績/診數：…\\暫存\\X健保統計\\
  - 自費抽成：…\\行政&工作事項\\醫師自費統計\\

輸出：scripts/salary_report_115Q1_v2.md
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

OUTPUT_FILE = Path(__file__).parent / "salary_report_115Q1_v2.md"

# 醫師基本資料
DOCTORS = {
    "周明毅": {"session_fee": 3231, "main_clinic": "澤豐", "is_director_in": "澤豐"},
    "呂敏盛": {"session_fee": 3167, "main_clinic": "澤豐", "is_director_in": None},
    "胡舒婷": {"session_fee": 3231, "main_clinic": "澤沛", "is_director_in": "澤沛"},
}

DIRECTOR_ALLOWANCE = 40000

# 自費統計欄位（澤豐 20 欄 / 澤沛 18 欄；C0-C17 在兩家都一樣）
CASH_COLS = {
    "日期": 0, "病歷號": 1, "姓名": 2, "病名": 3, "用藥": 4, "醫師": 5,
    "掛號費": 6, "內服藥": 7, "外用藥": 8, "針灸費": 9, "傷科費": 10,
    "脫臼費": 11, "保養費": 12, "飲片費": 13, "診察費": 14, "檢驗費": 15,
    "其它": 16, "自費合計": 17,
}

# 抽成率
COMMISSION_RATES = {
    "掛號費": 0.0,
    "內服藥": 0.20, "外用藥": 0.20, "保養費": 0.20, "飲片費": 0.20,
    "針灸費": 0.40, "傷科費": 0.40, "脫臼費": 0.40,
    "檢驗費": 0.10,
    # 診察費：周明毅 0.50、其他醫師 0.0（特殊處理）
}
DOCTOR_CONSULT_RATE = {
    "周明毅": 0.50,
    "呂敏盛": 0.0,
    "胡舒婷": 0.0,
}

# 業績獎金
PERF_TRIGGER_AVG = 15.1
PERF_INTERNAL_BASE = 7
PERF_INTERNAL_RATE = 150
PERF_PURE_BASE = 6
PERF_PURE_RATE = 80
PERF_COMBO_BASE = 2
PERF_COMBO_RATE = 110

# 業績獎金人次來源欄
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
    """單一醫師單月份自費統計總計"""
    clinic: str
    doctor: str
    yyyymm: str
    registration: int = 0
    internal_drug: int = 0    # 內服藥
    external_drug: int = 0    # 外用藥
    acupuncture: int = 0      # 針灸費
    trauma: int = 0           # 傷科費
    dislocation: int = 0      # 脫臼費
    maintenance: int = 0      # 保養費
    decoction: int = 0        # 飲片費
    consult: int = 0          # 診察費
    lab: int = 0              # 檢驗費
    other: int = 0
    total: int = 0
    source_file: str = ""


def parse_cash_report(file_path: Path, clinic: str, doctor: str, yyyymm: str) -> CashTotals:
    """讀取一份醫師自費統計檔，回傳 totals dataclass"""
    df = pd.read_excel(file_path, sheet_name=0, header=None)
    # 找 C0 == '總計' 的那一列
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


# 自費統計檔名 mapping（兩家命名規則不同）
def cash_path(clinic: str, doctor: str, yyyymm: str) -> Path | None:
    """回傳該醫師×月份的自費統計檔路徑；不存在回傳 None"""
    if clinic == "澤豐":
        # 11503\澤豐周明毅醫師自費統計11503.xlsx
        # 11503\澤豐胡舒婷醫師自費統計11503.xlsx
        # 11503\呂敏盛醫師自費統計11503.xlsx (沒澤豐 prefix)
        d = FZ_CASH_ROOT / yyyymm
        candidates = [
            d / f"澤豐{doctor}醫師自費統計{yyyymm}.xlsx",
            d / f"{doctor}醫師自費統計{yyyymm}.xlsx",
        ]
    else:  # 澤沛
        # 11501\11501月自費-周.xlsx, 11501月自費-胡.xlsx
        # 11502\11502自費-周.xlsx
        # 11503\11503自費-周.xlsx
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
    cash_commission: int = 0          # 自費抽成總額
    cash_commission_detail: dict = field(default_factory=dict)
    perf_internal: int = 0
    perf_pure: int = 0
    perf_combo: int = 0
    perf_triggered: bool = False
    perf_avg_visits: float = 0.0
    visit_count_nhi: int = 0
    cash_data_missing: bool = False    # 自費統計檔缺
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.director_allowance
            + self.session_pay
            + self.cash_commission
            + self.perf_internal
            + self.perf_pure
            + self.perf_combo
        )


def calc_cash_commission(ct: CashTotals) -> tuple[int, dict[str, int]]:
    """根據自費統計總計與各項抽成率計算抽成總額。"""
    detail = {}
    items = [
        ("內服藥", ct.internal_drug, COMMISSION_RATES["內服藥"]),
        ("外用藥", ct.external_drug, COMMISSION_RATES["外用藥"]),
        ("保養費", ct.maintenance, COMMISSION_RATES["保養費"]),
        ("飲片費", ct.decoction, COMMISSION_RATES["飲片費"]),
        ("針灸費", ct.acupuncture, COMMISSION_RATES["針灸費"]),
        ("傷科費", ct.trauma, COMMISSION_RATES["傷科費"]),
        ("脫臼費", ct.dislocation, COMMISSION_RATES["脫臼費"]),
        ("檢驗費", ct.lab, COMMISSION_RATES["檢驗費"]),
        ("診察費", ct.consult, DOCTOR_CONSULT_RATE.get(ct.doctor, 0.0)),
    ]
    total = 0
    for name, amt, rate in items:
        v = round(amt * rate)
        detail[name] = v
        total += v
    return total, detail


def calculate_salary(
    *,
    clinic: str,
    doctor: str,
    yyyymm: str,
    visit_row: pd.Series,
    cash: CashTotals | None,
) -> SalaryComponent:
    info = DOCTORS[doctor]
    sc = SalaryComponent(clinic=clinic, doctor=doctor, yyyymm=yyyymm)

    # 院長津貼
    if info["is_director_in"] == clinic:
        sc.director_allowance = DIRECTOR_ALLOWANCE

    # 診薪 × 診數
    sessions = _to_int(visit_row.get("診數合計", 0))
    sc.sessions = sessions
    sc.session_pay = sessions * info["session_fee"]

    # 自費抽成
    if cash is None:
        sc.cash_data_missing = True
        sc.notes.append(f"自費統計檔缺 ({yyyymm} {clinic} {doctor})")
    else:
        total, detail = calc_cash_commission(cash)
        sc.cash_commission = total
        sc.cash_commission_detail = detail

    # 業績獎金
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
                sc = calculate_salary(
                    clinic=clinic, doctor=doctor, yyyymm=ym,
                    visit_row=visit_row, cash=cash,
                )
                components.append(sc)
    return components, cash_totals


# ============================================================================
# 報表輸出
# ============================================================================


def render_md(comps: list[SalaryComponent], cash: list[CashTotals]) -> str:
    lines = []
    lines.append("# 醫師薪資試算 v2（115年 1-3月）")
    lines.append("")
    lines.append("> ⚠️ **試算性質，不入庫**。1-3月不含 A91+複針 獎金。")
    lines.append("> ⚠️ v2 修正：抽成只從「醫師自費統計」總計列計算，健保收入不抽成。")
    lines.append("")
    lines.append("## 試算規則")
    lines.append("")
    lines.append("**月薪 = 院長津貼 + 診薪×診數 + 自費抽成 + 業績獎金**")
    lines.append("")
    lines.append("| 項目 | 公式 |")
    lines.append("|---|---|")
    lines.append(f"| 院長津貼 | 主聘院長月領 NT$ {DIRECTOR_ALLOWANCE:,} |")
    lines.append("| 診薪 × 診數 | 周明毅 3,231 / 呂敏盛 3,167 / 胡舒婷 3,231 |")
    lines.append("")
    lines.append("**自費抽成（依「醫師自費統計」總計列各項目 × 比例）：**")
    lines.append("")
    lines.append("| 項目 | 周明毅 | 其他醫師 |")
    lines.append("|---|:---:|:---:|")
    lines.append("| 掛號費 | 0% | 0% |")
    lines.append("| 內服藥 / 外用藥 / 保養費 / 飲片費 | 20% | 20% |")
    lines.append("| 針灸費 / 傷科費 / 脫臼費 | 40% | 40% |")
    lines.append("| 診察費 | **50%** | **0%** |")
    lines.append("| 檢驗費 | 10% | 10% |")
    lines.append("")
    lines.append("**業績獎金（健保每診平均 ≥ 15.1 觸發；人次取自健保人數+初診統計）：**")
    lines.append("")
    lines.append("- 內科業績 = max(0, 內科健保人次 − 診數×7) × 150")
    lines.append("- 純針純傷 = max(0, (純針+純傷)健保人次 − 診數×6) × 80")
    lines.append("- 內+組合 = max(0, (內+針+內+傷)健保人次 − 診數×2) × 110")
    lines.append("")
    lines.append("**跨支援墊付（v1.2 規則）**")
    lines.append("")
    lines.append("- 周明毅（澤豐主聘）在澤沛支援 → 澤豐墊付，澤沛還澤豐")
    lines.append("- 胡舒婷（澤沛主聘）在澤豐支援 → 澤沛墊付，澤豐還澤沛")
    lines.append("- 呂敏盛（澤豐主聘）僅在澤豐看診")
    lines.append("")
    lines.append("---")

    months = sorted({c.yyyymm for c in comps})
    for ym in months:
        roc_y, roc_m = int(ym[:3]), int(ym[3:])
        ad_y = roc_y + 1911
        lines.append(f"\n## {ym}（西元 {ad_y}-{roc_m:02d}）")
        lines.append("")

        # 自費統計總計（金額對帳用）
        lines.append("### 自費統計總計（抽成輸入）")
        lines.append("")
        lines.append(
            "| 診所 | 醫師 | 內服 | 外用 | 針灸 | 傷科 | 脫臼 | 保養 | 飲片 | 診察 | 檢驗 | 自費合計 | 來源 |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---|")
        month_cash = [c for c in cash if c.yyyymm == ym]
        for c in sorted(month_cash, key=lambda x: (x.doctor, x.clinic)):
            lines.append(
                f"| {c.clinic} | {c.doctor} | {c.internal_drug:,} | {c.external_drug:,} | "
                f"{c.acupuncture:,} | {c.trauma:,} | {c.dislocation:,} | {c.maintenance:,} | "
                f"{c.decoction:,} | {c.consult:,} | {c.lab:,} | **{c.total:,}** | "
                f"`{c.source_file}` |"
            )
        # 標記缺資料
        missing = [c for c in comps if c.yyyymm == ym and c.cash_data_missing]
        if missing:
            lines.append("")
            lines.append("> ⚠️ **以下醫師當月自費統計檔缺失，抽成計入 0：**")
            for c in missing:
                lines.append(f"> - {c.clinic} {c.doctor}")
        lines.append("")

        # 分診所薪資明細
        lines.append("### 分診所薪資明細")
        lines.append("")
        lines.append(
            "| 診所 | 醫師 | 院長津貼 | 診數 | 診薪×診數 | 自費抽成 | 業績獎金 | 平均人次 | 觸發 | 小計 | 備註 |"
        )
        lines.append(
            "|---|---|---:|---:|---:|---:|---:|---:|:---:|---:|:---|"
        )
        month_comps = [c for c in comps if c.yyyymm == ym]
        for c in sorted(month_comps, key=lambda x: (x.doctor, x.clinic)):
            perf_total = c.perf_internal + c.perf_pure + c.perf_combo
            note = "⚠️ 自費資料缺" if c.cash_data_missing else ""
            lines.append(
                f"| {c.clinic} | {c.doctor} | {c.director_allowance:,} | {c.sessions} | "
                f"{c.session_pay:,} | {c.cash_commission:,} | {perf_total:,} | "
                f"{c.perf_avg_visits} | {'✅' if c.perf_triggered else '—'} | "
                f"**{c.total:,}** | {note} |"
            )
        lines.append("")

        # 業績獎金明細
        triggered = [c for c in month_comps if c.perf_triggered]
        if triggered:
            lines.append("### 業績獎金明細（觸發者）")
            lines.append("")
            lines.append("| 診所 | 醫師 | 內科業績 | 純針純傷業績 | 內+組合業績 | 小計 |")
            lines.append("|---|---|---:|---:|---:|---:|")
            for c in triggered:
                tot = c.perf_internal + c.perf_pure + c.perf_combo
                lines.append(
                    f"| {c.clinic} | {c.doctor} | {c.perf_internal:,} | "
                    f"{c.perf_pure:,} | {c.perf_combo:,} | **{tot:,}** |"
                )
            lines.append("")

        # 主聘彙總
        lines.append("### 醫師月薪彙總（主聘診所應付）")
        lines.append("")
        agg: dict[tuple[str, str], int] = {}
        for c in month_comps:
            main = DOCTORS[c.doctor]["main_clinic"]
            agg[(main, c.doctor)] = agg.get((main, c.doctor), 0) + c.total
        lines.append("| 主聘診所 | 醫師 | 月薪 |")
        lines.append("|---|---|---:|")
        for (main, doc), tot in sorted(agg.items()):
            lines.append(f"| {main} | {doc} | **{tot:,}** |")
        lines.append("")

        # 跨支援墊付
        lines.append("### 跨支援墊付（豐沛金流項目）")
        lines.append("")
        cross_pay: dict[tuple[str, str, str], int] = {}
        for c in month_comps:
            main = DOCTORS[c.doctor]["main_clinic"]
            if c.clinic != main and c.total > 0:
                cross_pay[(main, c.clinic, c.doctor)] = c.total
        if not cross_pay:
            lines.append("（無）")
        else:
            lines.append("| 墊付方（主聘） | 應由（看診診所）還 | 醫師 | 金額 |")
            lines.append("|---|---|---|---:|")
            for (main, vis, doc), amt in sorted(cross_pay.items()):
                lines.append(f"| {main} | {vis} | {doc} | {amt:,} |")
        lines.append("")
        lines.append("---")

    # 季彙總
    lines.append("\n## 1-3月季彙總（主聘診所應付）")
    lines.append("")
    q_agg: dict[tuple[str, str], int] = {}
    for c in comps:
        main = DOCTORS[c.doctor]["main_clinic"]
        q_agg[(main, c.doctor)] = q_agg.get((main, c.doctor), 0) + c.total
    lines.append("| 主聘診所 | 醫師 | 1-3月小計 | 備註 |")
    lines.append("|---|---|---:|:---|")
    for (main, doc), tot in sorted(q_agg.items()):
        # 看該醫師有幾個月缺自費資料
        miss_months = sorted({
            c.yyyymm for c in comps
            if c.doctor == doc and c.cash_data_missing
        })
        note = ""
        if miss_months:
            note = f"⚠️ 缺自費資料月份：{', '.join(miss_months)}"
        lines.append(f"| {main} | {doc} | **{tot:,}** | {note} |")
    lines.append("")

    # 與 v1 差異對比
    lines.append("\n## 與 v1 試算的差異")
    lines.append("")
    lines.append("v1 用門診申報金額統計報表的「自費(內科)+自費(針傷脫)」做抽成基底，金額被低估；")
    lines.append("v2 改用「醫師自費統計」總計列各項目分別×對應比例，較貼近實際。")
    lines.append("")
    lines.append("**驗算範例（11503 周明毅 澤豐 自費抽成）：**")
    lines.append("")
    lines.append("- 內服 139,053 × 20% = 27,811")
    lines.append("- 外用 8,970 × 20% = 1,794")
    lines.append("- 針灸 23,400 × 40% = 9,360")
    lines.append("- 保養 70,640 × 20% = 14,128")
    lines.append("- 飲片 680 × 20% = 136")
    lines.append("- 診察 3,750 × 50% = 1,875")
    lines.append("- 檢驗 0 × 10% = 0")
    lines.append("- **合計 = 55,104**")
    lines.append("")

    # 待確認
    lines.append("\n## ⚠️ 待院長確認")
    lines.append("")
    lines.append("1. **澤豐 11501 / 11502 醫師自費統計缺**")
    lines.append("   - 影響三位醫師（周明毅、呂敏盛、胡舒婷）在澤豐的 1-2月 抽成")
    lines.append("   - 目前先計入 0；補檔後可重算")
    lines.append("2. **業績獎金分子人次** 取自「健保人數+初診統計」")
    lines.append("   - 「內科」=該表內科健保人次（不含自費）")
    lines.append("   - 「內+針 / 內+傷」同表 — 是否正確？")
    lines.append("3. **「其它」項目（C16）目前不抽成** — 是否該抽？歸哪類？")
    return "\n".join(lines)


def main() -> int:
    months = ["11501", "11502", "11503"]
    comps, cash = run_simulation(months)
    print(f"comps={len(comps)} cash_totals={len(cash)}")
    md = render_md(comps, cash)
    OUTPUT_FILE.write_text(md, encoding="utf-8")
    print(f"v2 報告: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
