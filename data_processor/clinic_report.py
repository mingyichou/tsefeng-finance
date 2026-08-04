"""
門診申報金額統計報表 + A91+複針補表 匯入處理（Sprint 2.4）

對應資料字典 §3.1 / schema doctor_outpatient_summary

三種版式（澤豐自 11507 起改用與澤沛相同醫資系統，檔案結構與澤沛一致）：
  A. 澤豐 48 欄主表 — 含 A91+複針+整合照護（僅 ≤11506 舊系統）
     檔名：{YYYYMM}澤豐門診申報金額統計報表.xlsx
  B. 16 欄主表 — 不含 A91+複針，需配 137 欄補表
     （澤沛全期間；澤豐自 11507 起）
     檔名：{YYYYMM}澤豐|澤沛門診申報金額統計報表.xlsx
  C. A91+複針 137 欄補表（澤沛全期間；澤豐自 11507 起）
     檔名：{YYYYMM}澤豐|澤沛A91+複針.xlsx

⚠️ 寫入策略（呼叫端負責協調）：
  - 主表（A/B）整列 upsert 到 doctor_outpatient_summary
  - 補表（C）只更新 acu_complex_mid_count、acu_complex_high_count、a91_count 三欄
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import IO

import pandas as pd


# ─── 澤豐 48 欄 ────────────────────────────────────────────
FZ_COLS = {
    "醫師姓名": 1,
    "診察費": 2,
    "內科費": 3,
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
    "中度複針(人數)": 32,
    "高度複針(人數)": 34,
    "整合照護費(人)": 42,
}

# ─── 16 欄主表（澤沛全期間；澤豐自 11507 起同版式）──────
MAIN16_COLS = {
    "醫師姓名": 1,
    "診察費": 2,
    "藥費": 3,
    "調劑費": 4,
    "處置費": 5,
    "檢驗費": 6,
    "健保總額": 7,
    "自費內科": 8,
    "自費針傷脫": 9,
    "折扣": 10,
    "醫師統計": 11,
    "掛號費": 12,
    "部份負擔門部": 13,
    "部份負擔藥部": 14,
    "部份負擔傷部": 15,
}

# ─── 137 欄 A91+複針補表（資料從 R5；兩家同版式）─────────
A91_COLS = {
    "醫師姓名": 0,
    "A91人數": 6,
    "D05中複針+藥": 12,
    "D06中複針": 13,
    "D07高複針+藥": 14,
    "D08高複針": 15,
}


# ─── 檔名規則（兩家共用；澤豐依月份切換主表版式）─────────
# 月份後可帶一個英文字母後綴（新醫資系統匯出如「11507E澤豐…」）
MAIN_RE = re.compile(r"^(\d{5})[A-Za-z]?(澤豐|澤沛)門診申報金額統計報表\.xlsx$")
A91_RE  = re.compile(r"^(\d{5})[A-Za-z]?(澤豐|澤沛)A91\+複針\.xlsx$")

# 澤豐自 11507 起改用與澤沛相同醫資系統（16 欄主表 + 137 欄補表）
FZ_NEW_SYSTEM_FROM = "11507"
FZ_NEW_SYSTEM_FROM_MONTH = "2026-07-01"   # 同一分界的西元 service_month 形式


def _to_int(v) -> int:
    if pd.isna(v):
        return 0
    if isinstance(v, str):
        s = v.replace(",", "").strip()
        if not s or s in ("-", "—"):
            return 0
        try:
            return int(float(s))
        except ValueError:
            return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def detect_format(filename: str) -> dict:
    """
    從檔名識別版式並抽出 yyyymm。

    主表版式由診所×月份決定：
      澤豐 ≤11506 → fz_main48（48 欄舊系統）
      澤豐 ≥11507、澤沛全期間 → main16（需另配 a91_137 補表）

    Returns:
        {'kind': 'fz_main48' | 'main16' | 'a91_137',
         'clinic_short': '澤豐'|'澤沛',
         'yyyymm': '11503',
         'service_month': '2026-03-01'}
    """
    name = Path(filename).name
    m_main = MAIN_RE.match(name)
    m_a91 = A91_RE.match(name)
    m = m_main or m_a91
    if not m:
        raise ValueError(f"檔名格式不符 Sprint 2.4 任何版式：{name}")

    yyyymm, clinic = m.group(1), m.group(2)
    is_fz_legacy = clinic == "澤豐" and yyyymm < FZ_NEW_SYSTEM_FROM
    if m_main:
        kind = "fz_main48" if is_fz_legacy else "main16"
    else:
        if is_fz_legacy:
            raise ValueError(
                f"{name}：澤豐 {yyyymm} 屬舊醫資系統，48 欄主表已含 A91/複針，"
                f"不應有 A91+複針補表（澤豐自 {FZ_NEW_SYSTEM_FROM} 起才適用）"
            )
        kind = "a91_137"

    roc_y, roc_m = int(yyyymm[:3]), int(yyyymm[3:])
    return {
        "kind": kind,
        "clinic_short": clinic,
        "yyyymm": yyyymm,
        "service_month": f"{roc_y + 1911:04d}-{roc_m:02d}-01",
    }


def _is_data_doctor_row(name) -> bool:
    if pd.isna(name):
        return False
    s = str(name).strip()
    return bool(s) and "總" not in s and "合計" not in s


def parse_fz_main(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    name_to_doctor_id: dict[str, int],
) -> list[dict]:
    """澤豐 48 欄主表（≤11506 舊系統）→ list[dict]（含 A91/複針/整合）"""
    meta = detect_format(source_filename)
    if meta["kind"] != "fz_main48":
        raise ValueError(f"{source_filename}：版式不符 (期望 fz_main48)")

    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)
    if df.shape[1] < 43:
        raise ValueError(
            f"{source_filename}：僅 {df.shape[1]} 欄，不像 48 欄舊主表"
            "（新醫資系統匯出的 16 欄主表，檔名月份應為 11507 之後）"
        )

    records: list[dict] = []
    unknown: list[str] = []
    for r in range(4, df.shape[0]):
        name = df.iloc[r, FZ_COLS["醫師姓名"]]
        if not _is_data_doctor_row(name):
            continue
        name = str(name).strip()
        if name not in name_to_doctor_id:
            unknown.append(name)
            continue

        # 拆兩群：「處(內+xx)」4 欄 → combo；「純xx」4 欄 → pure
        # 用於醫師產值估算（澤豐：combo*0.3 + pure*0.5）
        combo_treatment = sum(
            _to_int(df.iloc[r, FZ_COLS[k]]) for k in (
                "處(內+傷)", "處(內+針)", "處(內+電)", "處(內+脫)",
            )
        )
        pure_treatment = sum(
            _to_int(df.iloc[r, FZ_COLS[k]]) for k in (
                "純傷科", "純針灸", "純電針", "純脫臼",
            )
        )
        treatment_fee = combo_treatment + pure_treatment

        records.append({
            "clinic_id": clinic_id,
            "doctor_id": name_to_doctor_id[name],
            "service_month": meta["service_month"],
            "nhi_consult_fee":     _to_int(df.iloc[r, FZ_COLS["診察費"]]),
            "nhi_drug_fee":        _to_int(df.iloc[r, FZ_COLS["內科費"]]),
            "nhi_dispense_fee":    _to_int(df.iloc[r, FZ_COLS["調劑費"]]),
            "nhi_treatment_fee":   treatment_fee,
            "nhi_combo_treatment": combo_treatment,
            "nhi_pure_treatment":  pure_treatment,
            "nhi_lab_fee":       _to_int(df.iloc[r, FZ_COLS["檢驗費"]]),
            "nhi_total_points":  _to_int(df.iloc[r, FZ_COLS["申報合計"]]),
            "cash_internal":     _to_int(df.iloc[r, FZ_COLS["自費(內科)"]]),
            "cash_acupuncture":  _to_int(df.iloc[r, FZ_COLS["自費(針傷脫)"]]),
            "registration_fee":  _to_int(df.iloc[r, FZ_COLS["掛號費"]]),
            "copay_outpatient":  _to_int(df.iloc[r, FZ_COLS["部分負擔"]]),
            # 澤豐主表已含 A91/複針/整合
            "acu_complex_mid_count":  _to_int(df.iloc[r, FZ_COLS["中度複針(人數)"]]),
            "acu_complex_high_count": _to_int(df.iloc[r, FZ_COLS["高度複針(人數)"]]),
            "a91_count":              _to_int(df.iloc[r, FZ_COLS["整合照護費(人)"]]),
        })

    if unknown:
        raise ValueError(f"{source_filename}：醫師不在 doctors 表：{unknown}")
    return records


def parse_main16(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    name_to_doctor_id: dict[str, int],
) -> list[dict]:
    """16 欄主表（澤沛全期間；澤豐 11507 起）→ list[dict]
    （不含 A91/複針，需另用 a91_137 補表）"""
    meta = detect_format(source_filename)
    if meta["kind"] != "main16":
        raise ValueError(f"{source_filename}：版式不符 (期望 main16)")

    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)
    if df.shape[1] >= 30:
        raise ValueError(
            f"{source_filename}：{df.shape[1]} 欄，看起來是澤豐 48 欄舊版主表"
            "（舊系統匯出的檔名月份應為 11506 之前）"
        )
    if df.shape[1] < 16:
        raise ValueError(f"{source_filename}：僅 {df.shape[1]} 欄，不足 16 欄主表")

    records: list[dict] = []
    unknown: list[str] = []
    for r in range(5, df.shape[0]):  # 兩列表頭，資料從 R5
        name = df.iloc[r, MAIN16_COLS["醫師姓名"]]
        if not _is_data_doctor_row(name):
            continue
        name = str(name).strip()
        if name not in name_to_doctor_id:
            unknown.append(name)
            continue

        records.append({
            "clinic_id": clinic_id,
            "doctor_id": name_to_doctor_id[name],
            "service_month": meta["service_month"],
            "nhi_consult_fee":   _to_int(df.iloc[r, MAIN16_COLS["診察費"]]),
            "nhi_drug_fee":      _to_int(df.iloc[r, MAIN16_COLS["藥費"]]),
            "nhi_dispense_fee":  _to_int(df.iloc[r, MAIN16_COLS["調劑費"]]),
            "nhi_treatment_fee": _to_int(df.iloc[r, MAIN16_COLS["處置費"]]),
            "nhi_lab_fee":       _to_int(df.iloc[r, MAIN16_COLS["檢驗費"]]),
            "nhi_total_points":  _to_int(df.iloc[r, MAIN16_COLS["健保總額"]]),
            "cash_internal":     _to_int(df.iloc[r, MAIN16_COLS["自費內科"]]),
            "cash_acupuncture":  _to_int(df.iloc[r, MAIN16_COLS["自費針傷脫"]]),
            "cash_discount":     _to_int(df.iloc[r, MAIN16_COLS["折扣"]]),
            "doctor_total":      _to_int(df.iloc[r, MAIN16_COLS["醫師統計"]]),
            "registration_fee":  _to_int(df.iloc[r, MAIN16_COLS["掛號費"]]),
            "copay_outpatient":  _to_int(df.iloc[r, MAIN16_COLS["部份負擔門部"]]),
            "copay_drug":        _to_int(df.iloc[r, MAIN16_COLS["部份負擔藥部"]]),
            "copay_trauma":      _to_int(df.iloc[r, MAIN16_COLS["部份負擔傷部"]]),
            # A91/複針/整合 從 a91_137 補表更新（這裡先給 0）
            "acu_complex_mid_count":  0,
            "acu_complex_high_count": 0,
            "a91_count":              0,
        })

    if unknown:
        raise ValueError(f"{source_filename}：醫師不在 doctors 表：{unknown}")
    return records


def parse_a91(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    name_to_doctor_id: dict[str, int],
) -> list[dict]:
    """
    137 欄 A91+複針補表（澤沛全期間；澤豐 11507 起）→ list[dict]，
    僅含三個欄位 + 識別欄。

    對應到主表後，只 UPDATE acu_complex_mid_count/high_count/a91_count。
    """
    meta = detect_format(source_filename)
    if meta["kind"] != "a91_137":
        raise ValueError(f"{source_filename}：版式不符 (期望 a91_137)")

    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)
    if df.shape[1] < 100:
        raise ValueError(
            f"{source_filename}：僅 {df.shape[1]} 欄，不像 137 欄 A91+複針補表"
            "（請確認沒有把主表誤命名為 A91+複針）"
        )

    records: list[dict] = []
    unknown: list[str] = []
    for r in range(5, df.shape[0]):
        name = df.iloc[r, A91_COLS["醫師姓名"]]
        if not _is_data_doctor_row(name):
            continue
        name = str(name).strip()
        if name not in name_to_doctor_id:
            unknown.append(name)
            continue

        mid = (
            _to_int(df.iloc[r, A91_COLS["D05中複針+藥"]])
            + _to_int(df.iloc[r, A91_COLS["D06中複針"]])
        )
        high = (
            _to_int(df.iloc[r, A91_COLS["D07高複針+藥"]])
            + _to_int(df.iloc[r, A91_COLS["D08高複針"]])
        )
        a91 = _to_int(df.iloc[r, A91_COLS["A91人數"]])

        records.append({
            "clinic_id": clinic_id,
            "doctor_id": name_to_doctor_id[name],
            "service_month": meta["service_month"],
            "acu_complex_mid_count": mid,
            "acu_complex_high_count": high,
            "a91_count": a91,
        })

    if unknown:
        raise ValueError(f"{source_filename}：醫師不在 doctors 表：{unknown}")
    return records
