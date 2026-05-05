"""
醫師薪資試算 v1（115/01-115/03）

⚠️ 試算性質 — 不入庫，只產出 markdown 報告給院長對帳
⚠️ 1-3月為新制（A91+複針）上線前，故跳過 A91+複針 獎金；4月起再加

資料來源（Dropbox 暫存）：
  C:\\Users\\User\\Dropbox\\家庭資料室\\澤豐診所行政表單(櫃台電腦)\\暫存\\澤豐健保統計\\
  C:\\Users\\User\\Dropbox\\家庭資料室\\澤沛診所行政表單(櫃台電腦)\\暫存\\澤沛健保統計\\

輸出：scripts/salary_report_115Q1.md
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


# ============================================================================
# 常數
# ============================================================================

FZ_ROOT = Path(
    r"C:\Users\User\Dropbox\家庭資料室\澤豐診所行政表單(櫃台電腦)\暫存\澤豐健保統計"
)
FP_ROOT = Path(
    r"C:\Users\User\Dropbox\家庭資料室\澤沛診所行政表單(櫃台電腦)\暫存\澤沛健保統計"
)

OUTPUT_FILE = Path(__file__).parent / "salary_report_115Q1.md"

# 醫師基本資料
DOCTORS = {
    "周明毅": {"session_fee": 3231, "main_clinic": "澤豐", "is_director_in": "澤豐"},
    "呂敏盛": {"session_fee": 3167, "main_clinic": "澤豐", "is_director_in": None},
    "胡舒婷": {"session_fee": 3231, "main_clinic": "澤沛", "is_director_in": "澤沛"},
}

DIRECTOR_ALLOWANCE = 40000

# 抽成率（依大類）
COMMISSION_INTERNAL = 0.20   # 內科 / 自費內科 — 內服/外用/保養/飲片
COMMISSION_TREATMENT = 0.40  # 處置費 / 自費針傷脫 — 針灸/傷科/脫臼
COMMISSION_LAB = 0.10        # 檢驗
COMMISSION_CONSULT_DIRECTOR = 0.50  # 診察費僅周院長/胡院長 50%；其他醫師 0%

# 業績獎金
PERF_TRIGGER_AVG = 15.1   # 每診平均人次（健保）≥ 15.1 才觸發
PERF_INTERNAL_BASE = 7    # 內科 base 每診 7 人
PERF_INTERNAL_RATE = 150
PERF_PURE_BASE = 6        # 純針+純傷 base 每診 6 人
PERF_PURE_RATE = 80
PERF_COMBO_BASE = 2       # 內+針+內+傷 base 每診 2 人
PERF_COMBO_RATE = 110


# ============================================================================
# Parser: 門診申報金額統計報表
# ============================================================================

# 澤豐 48 欄
FZ_REPORT_COLS = {
    "醫師姓名": 1,
    "診察費": 2,
    "內科費": 3,            # 健保內科（內服/外用/保養/飲片）
    "處(內+傷)": 4,
    "處(內+針)": 5,
    "處(內+電)": 6,
    "處(內+脫)": 7,
    "純傷科": 8,
    "純針灸": 9,
    "純電針": 10,
    "純脫臼": 11,
    "調劑費": 12,
    "檢驗費": 13,
    "申報合計": 14,
    "部分負擔": 15,
    "申報金額": 16,
    "掛號費": 17,
    "自費(內科)": 18,
    "自費(針傷脫)": 19,
    "看診天數": 20,
    "看診總人數": 21,
    "含診察費人次": 22,
}

# 澤沛 16 欄
FP_REPORT_COLS = {
    "醫師姓名": 1,
    "診察費": 2,
    "藥費": 3,         # = 內科費（澤沛只給合併值）
    "調劑費": 4,
    "處置費": 5,        # = 純針/傷/脫 + 處(內+X) 之合併（無細分）
    "檢驗費": 6,
    "健保總額": 7,
    "自費內科": 8,
    "自費針傷脫": 9,
    "折扣": 10,
    "醫師統計": 11,
    "掛號費": 12,
}


def _to_int(v) -> int:
    if pd.isna(v):
        return 0
    if isinstance(v, str):
        v = v.replace(",", "").strip()
        if not v or v in ("-", "—"):
            return 0
        try:
            return int(float(v))
        except ValueError:
            return 0
    return int(v)


def parse_fz_report(file_path: Path) -> pd.DataFrame:
    """澤豐門診申報金額統計報表 → 每醫師一列"""
    df = pd.read_excel(file_path, sheet_name=0, header=None)
    rows = []
    for r in range(4, df.shape[0]):
        name = df.iloc[r, FZ_REPORT_COLS["醫師姓名"]]
        if pd.isna(name) or "總" in str(name):
            continue
        rec = {"clinic": "澤豐", "醫師": str(name).strip()}
        for k, c in FZ_REPORT_COLS.items():
            if k == "醫師姓名":
                continue
            rec[k] = _to_int(df.iloc[r, c])
        rows.append(rec)
    return pd.DataFrame(rows)


def parse_fp_report(file_path: Path) -> pd.DataFrame:
    """澤沛門診申報金額統計報表（16 欄不含 A91+複針）"""
    df = pd.read_excel(file_path, sheet_name=0, header=None)
    rows = []
    for r in range(5, df.shape[0]):  # 澤沛兩列表頭，資料從 R5
        name = df.iloc[r, FP_REPORT_COLS["醫師姓名"]]
        if pd.isna(name) or "總" in str(name):
            continue
        rec = {"clinic": "澤沛", "醫師": str(name).strip()}
        for k, c in FP_REPORT_COLS.items():
            if k == "醫師姓名":
                continue
            rec[k] = _to_int(df.iloc[r, c])
        rows.append(rec)
    return pd.DataFrame(rows)


# ============================================================================
# Parser: 健保人數+初診統計
# ============================================================================

VISIT_COLS = {
    "醫師姓名": 1,
    "內科": 2,
    "純針": 3,
    "純傷": 4,
    "內+針": 5,
    "內+傷": 6,
    "健保總數": 7,
    "自費內科": 8,
    "自費針傷": 9,
    "合計": 10,
    "診數合計": 16,
}


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
    """單一診所單一醫師單月份薪資明細"""
    clinic: str
    doctor: str
    yyyymm: str
    director_allowance: int = 0
    session_pay: int = 0          # 診薪 × 診數
    commission_consult: int = 0    # 診察費抽成
    commission_internal: int = 0   # 內科 + 自費內科 抽成
    commission_treatment: int = 0  # 處置費 + 自費針傷脫 抽成
    commission_lab: int = 0        # 檢驗費抽成
    perf_internal: int = 0         # 業績獎金: 內科
    perf_pure: int = 0             # 業績獎金: 純針純傷
    perf_combo: int = 0            # 業績獎金: 內+組合
    perf_triggered: bool = False
    perf_avg_visits: float = 0.0
    sessions: int = 0
    visit_count: int = 0           # 健保總人次
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.director_allowance
            + self.session_pay
            + self.commission_consult
            + self.commission_internal
            + self.commission_treatment
            + self.commission_lab
            + self.perf_internal
            + self.perf_pure
            + self.perf_combo
        )


def calculate_salary(
    *,
    clinic: str,
    doctor: str,
    yyyymm: str,
    report_row: pd.Series,
    visit_row: pd.Series,
) -> SalaryComponent:
    """
    根據單一醫師於單一診所單月份的兩份資料試算薪資。

    報表欄位來自 門診申報金額統計報表（金額）。
    人次欄位來自 健保人數&初診統計（業績獎金 trigger + 公式）。
    """
    info = DOCTORS[doctor]
    sc = SalaryComponent(clinic=clinic, doctor=doctor, yyyymm=yyyymm)

    # ─── 院長津貼 ───
    if info["is_director_in"] == clinic:
        sc.director_allowance = DIRECTOR_ALLOWANCE

    # ─── 診薪 × 診數 ───
    sessions = _to_int(visit_row.get("診數合計", 0))
    sc.sessions = sessions
    sc.session_pay = sessions * info["session_fee"]

    # ─── 抽成 ───
    if clinic == "澤豐":
        # 內科金額 = 內科費 (健保) + 自費內科
        nhi_internal = _to_int(report_row["內科費"])
        cash_internal = _to_int(report_row["自費(內科)"])
        # 處置費 = 處(內+傷/針/電/脫) + 純傷/針/電/脫
        nhi_treatment = sum(
            _to_int(report_row[k]) for k in
            ["處(內+傷)", "處(內+針)", "處(內+電)", "處(內+脫)",
             "純傷科", "純針灸", "純電針", "純脫臼"]
        )
        cash_treatment = _to_int(report_row["自費(針傷脫)"])
        consult_fee = _to_int(report_row["診察費"])
        lab_fee = _to_int(report_row["檢驗費"])
    else:  # 澤沛
        nhi_internal = _to_int(report_row["藥費"])
        cash_internal = _to_int(report_row["自費內科"])
        nhi_treatment = _to_int(report_row["處置費"])
        cash_treatment = _to_int(report_row["自費針傷脫"])
        consult_fee = _to_int(report_row["診察費"])
        lab_fee = _to_int(report_row["檢驗費"])

    sc.commission_internal = round(
        (nhi_internal + cash_internal) * COMMISSION_INTERNAL
    )
    sc.commission_treatment = round(
        (nhi_treatment + cash_treatment) * COMMISSION_TREATMENT
    )
    sc.commission_lab = round(lab_fee * COMMISSION_LAB)

    # 診察費抽成：僅院長 50%，其他醫師 0%
    if info["is_director_in"] == clinic:
        sc.commission_consult = round(consult_fee * COMMISSION_CONSULT_DIRECTOR)
    else:
        sc.commission_consult = 0

    # ─── 業績獎金 ───
    nhi_total_visits = _to_int(visit_row.get("健保總數", 0))
    sc.visit_count = nhi_total_visits

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

            sc.perf_internal = max(
                0, v_internal - sessions * PERF_INTERNAL_BASE
            ) * PERF_INTERNAL_RATE
            sc.perf_pure = max(
                0, (v_pure_zhen + v_pure_shang) - sessions * PERF_PURE_BASE
            ) * PERF_PURE_RATE
            sc.perf_combo = max(
                0, (v_combo_zhen + v_combo_shang) - sessions * PERF_COMBO_BASE
            ) * PERF_COMBO_RATE

    return sc


# ============================================================================
# 主流程
# ============================================================================


def load_month_data(yyyymm: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    讀取一個月（如 '11501'）的所有資料，回傳 (報表 df, 人次 df)
    兩份各含兩家診所的醫師列。
    """
    fz_report = parse_fz_report(
        FZ_ROOT / "澤豐門診申報金額統計報表" / f"{yyyymm}澤豐門診申報金額統計報表.xlsx"
    )
    fp_report = parse_fp_report(
        FP_ROOT / "澤沛門診申報金額統計報表(不含A91+複針)"
        / f"{yyyymm}澤沛門診申報金額統計報表.xlsx"
    )
    fz_visit = parse_visit_count(
        FZ_ROOT / "澤豐健保人數&初診統計" / f"{yyyymm}澤豐健保人數&初診統計.xlsx",
        "澤豐",
    )
    fp_visit = parse_visit_count(
        FP_ROOT / "澤沛健保人數&初診統計" / f"{yyyymm}澤沛健保人數&初診統計.xlsx",
        "澤沛",
    )
    return (
        pd.concat([fz_report, fp_report], ignore_index=True),
        pd.concat([fz_visit, fp_visit], ignore_index=True),
    )


def run_simulation(months: list[str]) -> list[SalaryComponent]:
    out: list[SalaryComponent] = []
    for ym in months:
        report_df, visit_df = load_month_data(ym)

        # 索引：(clinic, 醫師)
        rep_idx = report_df.set_index(["clinic", "醫師"])
        vis_idx = visit_df.set_index(["clinic", "醫師"])

        # 對每個 (clinic, 醫師) 都試算
        all_keys = set(rep_idx.index) | set(vis_idx.index)
        for clinic, doctor in sorted(all_keys):
            if doctor not in DOCTORS:
                continue
            try:
                rep_row = rep_idx.loc[(clinic, doctor)]
                vis_row = vis_idx.loc[(clinic, doctor)]
            except KeyError:
                continue
            sc = calculate_salary(
                clinic=clinic,
                doctor=doctor,
                yyyymm=ym,
                report_row=rep_row,
                visit_row=vis_row,
            )
            out.append(sc)
    return out


# ============================================================================
# 報表輸出
# ============================================================================


def render_md(components: list[SalaryComponent]) -> str:
    lines = [
        "# 醫師薪資試算（115年 1-3月）",
        "",
        "> ⚠️ **試算性質，不入庫**。1-3月不含 A91+複針 獎金（115/04 新制起算前）。",
        "",
        "## 試算規則摘要",
        "",
        "| 項目 | 公式 |",
        "|---|---|",
        f"| 院長津貼 | 主聘院長 NT$ {DIRECTOR_ALLOWANCE:,} |",
        "| 診薪 × 診數 | 周明毅 3,231 / 呂敏盛 3,167 / 胡舒婷 3,231 |",
        f"| 內科抽成 (內服/外用/保養/飲片 + 自費內科) | × {COMMISSION_INTERNAL:.0%} |",
        f"| 處置費抽成 (針灸/傷科/脫臼 + 自費針傷脫) | × {COMMISSION_TREATMENT:.0%} |",
        f"| 檢驗費抽成 | × {COMMISSION_LAB:.0%} |",
        f"| 診察費抽成 | 主聘院長 × {COMMISSION_CONSULT_DIRECTOR:.0%}，其他醫師 0% |",
        f"| 業績獎金觸發條件 | 健保每診平均 ≥ {PERF_TRIGGER_AVG} |",
        f"| 內科業績 | max(0, 內科人次 − 診數×{PERF_INTERNAL_BASE}) × {PERF_INTERNAL_RATE} |",
        f"| 純針純傷業績 | max(0, (純針+純傷) − 診數×{PERF_PURE_BASE}) × {PERF_PURE_RATE} |",
        f"| 內+組合業績 | max(0, (內+針+內+傷) − 診數×{PERF_COMBO_BASE}) × {PERF_COMBO_RATE} |",
        "",
        "## 跨支援墊付邏輯（v1.2 規則）",
        "",
        "- 周明毅（澤豐主聘）在澤沛支援 → 澤豐墊付，澤沛還澤豐",
        "- 胡舒婷（澤沛主聘）在澤豐支援 → 澤沛墊付，澤豐還澤沛",
        "- 呂敏盛（澤豐主聘）僅在澤豐看診",
        "- 試算先以「看診診所」分開列出，再彙總到主聘診所為應付薪資",
        "",
    ]

    # 按月份分節
    months = sorted({c.yyyymm for c in components})
    for ym in months:
        roc_y, roc_m = int(ym[:3]), int(ym[3:])
        ad_y = roc_y + 1911
        lines.append(f"---\n\n## {ym}（西元 {ad_y}-{roc_m:02d}）\n")

        # 表 1：分診所明細
        lines.append("### 分診所薪資明細\n")
        lines.append(
            "| 診所 | 醫師 | 院長津貼 | 診數 | 診薪×診數 | 診察費抽成 | 內科抽成 | 處置抽成 | 檢驗抽成 | 業績內科 | 業績純針傷 | 業績內+組合 | 平均人次 | 觸發 | 小計 |"
        )
        lines.append(
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---:|"
        )
        month_comps = [c for c in components if c.yyyymm == ym]
        for c in sorted(month_comps, key=lambda x: (x.doctor, x.clinic)):
            lines.append(
                f"| {c.clinic} | {c.doctor} | "
                f"{c.director_allowance:,} | {c.sessions} | {c.session_pay:,} | "
                f"{c.commission_consult:,} | {c.commission_internal:,} | "
                f"{c.commission_treatment:,} | {c.commission_lab:,} | "
                f"{c.perf_internal:,} | {c.perf_pure:,} | {c.perf_combo:,} | "
                f"{c.perf_avg_visits} | "
                f"{'✅' if c.perf_triggered else '—'} | "
                f"**{c.total:,}** |"
            )
        lines.append("")

        # 表 2：彙總到主聘診所
        lines.append("### 醫師月薪彙總（依主聘診所應付）\n")
        agg: dict[tuple[str, str], int] = {}
        for c in month_comps:
            main = DOCTORS[c.doctor]["main_clinic"]
            agg[(main, c.doctor)] = agg.get((main, c.doctor), 0) + c.total
        lines.append("| 主聘診所 | 醫師 | 月薪 |")
        lines.append("|---|---|---:|")
        for (main, doc), total in sorted(agg.items()):
            lines.append(f"| {main} | {doc} | **{total:,}** |")
        lines.append("")

        # 表 3：跨支援墊付方向
        lines.append("### 跨支援墊付（豐沛金流項目）\n")
        cross_pay: dict[tuple[str, str, str], int] = {}
        for c in month_comps:
            main = DOCTORS[c.doctor]["main_clinic"]
            if c.clinic != main and c.total > 0:
                # 看診診所 ≠ 主聘 → 主聘墊付，看診診所還主聘
                cross_pay[(main, c.clinic, c.doctor)] = c.total
        if not cross_pay:
            lines.append("（無）\n")
        else:
            lines.append(
                "| 墊付方（主聘） | 應由（看診診所）還 | 醫師 | 金額 |"
            )
            lines.append("|---|---|---|---:|")
            for (main, visited, doc), amt in sorted(cross_pay.items()):
                lines.append(f"| {main} | {visited} | {doc} | {amt:,} |")
            lines.append("")

    # 季彙總
    lines.append("---\n\n## 1-3月季彙總（主聘診所應付）\n")
    q_agg: dict[tuple[str, str], int] = {}
    for c in components:
        main = DOCTORS[c.doctor]["main_clinic"]
        q_agg[(main, c.doctor)] = q_agg.get((main, c.doctor), 0) + c.total
    lines.append("| 主聘診所 | 醫師 | 1-3月小計 |")
    lines.append("|---|---|---:|")
    for (main, doc), total in sorted(q_agg.items()):
        lines.append(f"| {main} | {doc} | **{total:,}** |")
    lines.append("")

    # 待釐清
    lines.append("\n---\n\n## ⚠️ 待院長確認")
    lines.append("""
1. **抽成分類**：
   - 「處(內+傷/針/電/脫)」整個歸 40%（針傷脫）— 沒拆「內」部分到 20%
   - 澤沛「處置費」C5 同樣整個 40%
   - 「調劑費」目前未抽成 — 是否該抽？歸哪類？
2. **業績獎金分母**：用「健保總人次」還是「合計（含自費）」？目前用健保總人次
3. **業績獎金分子人次**：來自「健保人數+初診統計」表。「內科」=該表內科人次（不含自費）；「內+針/內+傷」同表
4. **診察費抽成**：僅澤豐周院長（在澤豐）+ 澤沛胡院長（在澤沛）拿 50%；周明毅在澤沛支援、胡舒婷在澤豐支援的診察費 → 不抽。是否正確？
5. **跨支援墊付方向**：列在「跨支援墊付」表 — 數字大時對豐沛金流影響大，請確認方向
""")

    return "\n".join(lines)


def main() -> int:
    months = ["11501", "11502", "11503"]
    print(f"試算月份: {months}")
    components = run_simulation(months)
    print(f"產出 {len(components)} 筆醫師×診所×月份明細")

    md = render_md(components)
    OUTPUT_FILE.write_text(md, encoding="utf-8")
    print(f"報告已寫入: {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
