"""
門診申報金額統計報表 + A91+複針補表 匯入處理（Sprint 2.4）

對應資料字典 §3.1 / schema doctor_outpatient_summary

三種版式：
  A. 澤豐 48 欄 — 含 A91+複針+整合照護
     檔名：{YYYYMM}澤豐門診申報金額統計報表.xlsx
  B. 澤沛 16 欄 — 不含 A91+複針，需配 137 欄補表
     檔名：{YYYYMM}澤沛門診申報金額統計報表.xlsx
  C. 澤沛 A91+複針 137 欄補表
     檔名：{YYYYMM}澤沛A91+複針.xlsx

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

# ─── 澤沛 16 欄 ────────────────────────────────────────────
FP_COLS = {
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

# ─── 澤沛 137 欄 A91+複針補表（資料從 R5）───────────────
FP_A91_COLS = {
    "醫師姓名": 0,
    "A91人數": 6,
    "D05中複針+藥": 12,
    "D06中複針": 13,
    "D07高複針+藥": 14,
    "D08高複針": 15,
}


# ─── 檔名規則 ───────────────────────────────────────────
FZ_MAIN_RE = re.compile(r"^(\d{5})澤豐門診申報金額統計報表\.xlsx$")
FP_MAIN_RE = re.compile(r"^(\d{5})澤沛門診申報金額統計報表\.xlsx$")
FP_A91_RE  = re.compile(r"^(\d{5})澤沛A91\+複針\.xlsx$")


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

    Returns:
        {'kind': 'fz_main' | 'fp_main' | 'fp_a91',
         'clinic_short': '澤豐'|'澤沛',
         'yyyymm': '11503',
         'service_month': '2026-03-01'}
    """
    name = Path(filename).name
    for pat, kind, clinic in [
        (FZ_MAIN_RE, "fz_main", "澤豐"),
        (FP_MAIN_RE, "fp_main", "澤沛"),
        (FP_A91_RE, "fp_a91", "澤沛"),
    ]:
        m = pat.match(name)
        if m:
            yyyymm = m.group(1)
            roc_y, roc_m = int(yyyymm[:3]), int(yyyymm[3:])
            return {
                "kind": kind,
                "clinic_short": clinic,
                "yyyymm": yyyymm,
                "service_month": f"{roc_y + 1911:04d}-{roc_m:02d}-01",
            }
    raise ValueError(f"檔名格式不符 Sprint 2.4 任何版式：{name}")


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
    """澤豐 48 欄主表 → list[dict] 對應 doctor_outpatient_summary（含 A91/複針/整合）"""
    meta = detect_format(source_filename)
    if meta["kind"] != "fz_main":
        raise ValueError(f"{source_filename}：版式不符 (期望 fz_main)")

    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

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

        treatment_fee = sum(
            _to_int(df.iloc[r, FZ_COLS[k]]) for k in (
                "處(內+傷)", "處(內+針)", "處(內+電)", "處(內+脫)",
                "純傷科", "純針灸", "純電針", "純脫臼",
            )
        )

        records.append({
            "clinic_id": clinic_id,
            "doctor_id": name_to_doctor_id[name],
            "service_month": meta["service_month"],
            "nhi_consult_fee":   _to_int(df.iloc[r, FZ_COLS["診察費"]]),
            "nhi_drug_fee":      _to_int(df.iloc[r, FZ_COLS["內科費"]]),
            "nhi_dispense_fee":  _to_int(df.iloc[r, FZ_COLS["調劑費"]]),
            "nhi_treatment_fee": treatment_fee,
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


def parse_fp_main(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    name_to_doctor_id: dict[str, int],
) -> list[dict]:
    """澤沛 16 欄主表 → list[dict]（不含 A91/複針，需另用 fp_a91 補表）"""
    meta = detect_format(source_filename)
    if meta["kind"] != "fp_main":
        raise ValueError(f"{source_filename}：版式不符 (期望 fp_main)")

    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    records: list[dict] = []
    unknown: list[str] = []
    for r in range(5, df.shape[0]):  # 澤沛兩列表頭，資料從 R5
        name = df.iloc[r, FP_COLS["醫師姓名"]]
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
            "nhi_consult_fee":   _to_int(df.iloc[r, FP_COLS["診察費"]]),
            "nhi_drug_fee":      _to_int(df.iloc[r, FP_COLS["藥費"]]),
            "nhi_dispense_fee":  _to_int(df.iloc[r, FP_COLS["調劑費"]]),
            "nhi_treatment_fee": _to_int(df.iloc[r, FP_COLS["處置費"]]),
            "nhi_lab_fee":       _to_int(df.iloc[r, FP_COLS["檢驗費"]]),
            "nhi_total_points":  _to_int(df.iloc[r, FP_COLS["健保總額"]]),
            "cash_internal":     _to_int(df.iloc[r, FP_COLS["自費內科"]]),
            "cash_acupuncture":  _to_int(df.iloc[r, FP_COLS["自費針傷脫"]]),
            "cash_discount":     _to_int(df.iloc[r, FP_COLS["折扣"]]),
            "doctor_total":      _to_int(df.iloc[r, FP_COLS["醫師統計"]]),
            "registration_fee":  _to_int(df.iloc[r, FP_COLS["掛號費"]]),
            "copay_outpatient":  _to_int(df.iloc[r, FP_COLS["部份負擔門部"]]),
            "copay_drug":        _to_int(df.iloc[r, FP_COLS["部份負擔藥部"]]),
            "copay_trauma":      _to_int(df.iloc[r, FP_COLS["部份負擔傷部"]]),
            # A91/複針/整合 從 fp_a91 補表更新（這裡先給 0）
            "acu_complex_mid_count":  0,
            "acu_complex_high_count": 0,
            "a91_count":              0,
        })

    if unknown:
        raise ValueError(f"{source_filename}：醫師不在 doctors 表：{unknown}")
    return records


def parse_fp_a91(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    name_to_doctor_id: dict[str, int],
) -> list[dict]:
    """
    澤沛 137 欄 A91+複針補表 → list[dict]，僅含三個欄位 + 識別欄。

    對應到主表後，只 UPDATE acu_complex_mid_count/high_count/a91_count。
    """
    meta = detect_format(source_filename)
    if meta["kind"] != "fp_a91":
        raise ValueError(f"{source_filename}：版式不符 (期望 fp_a91)")

    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    records: list[dict] = []
    unknown: list[str] = []
    for r in range(5, df.shape[0]):
        name = df.iloc[r, FP_A91_COLS["醫師姓名"]]
        if not _is_data_doctor_row(name):
            continue
        name = str(name).strip()
        if name not in name_to_doctor_id:
            unknown.append(name)
            continue

        mid = (
            _to_int(df.iloc[r, FP_A91_COLS["D05中複針+藥"]])
            + _to_int(df.iloc[r, FP_A91_COLS["D06中複針"]])
        )
        high = (
            _to_int(df.iloc[r, FP_A91_COLS["D07高複針+藥"]])
            + _to_int(df.iloc[r, FP_A91_COLS["D08高複針"]])
        )
        a91 = _to_int(df.iloc[r, FP_A91_COLS["A91人數"]])

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
