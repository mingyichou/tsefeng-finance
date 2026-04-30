"""
健保人數+初診統計匯入處理（Sprint 2.5）

對應資料字典 §3.2 / schema:
  - doctor_visit_stats（醫師月度看診人數+診數，薪資計算的關鍵輸入）
  - clinic_visit_rates（診所月度初診率/自費率/掛號優免率等）

檔案結構：
  R0: 機構碼+診所名
  R1: "115年MM月01日至XX日健保門診人數統計三"
  R2: 列印日期
  R3-R4: 兩列表頭（合併欄分組）
  R5-Rn: 醫師資料列（C1=醫師姓名）
  接著: '合計：' 列（不含醫師名）
  最後段: 初診/複診/自費/特約卡/掛號優免 等診所彙總

兩家版式：
  - 澤豐：17 欄
  - 澤沛：23 欄（多 6 欄：初診/初診率/複診/複診率/健保金額/自費金額）
  - C1-C16 兩家相同結構

檔名規則：{YYYYMM}{澤豐|澤沛}健保人數&初診統計.xlsx
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import IO

import pandas as pd


# 醫師資料列欄位 mapping（C1-C16 兩家共用）
DOCTOR_COLS = {
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
    "針傷科第1次": 11,
    # C12 是日期欄（datetime，不入庫）
    "早": 13,
    "中": 14,
    "晚": 15,
    "診數合計": 16,
}


FILENAME_RE = re.compile(
    r"^(\d{5})(澤豐|澤沛)健保人數&初診統計\.xlsx$"
)


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


def _to_float(v) -> float | None:
    if pd.isna(v):
        return None
    if isinstance(v, str):
        s = v.strip().rstrip("%")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_filename(filename: str) -> dict:
    """
    從檔名解析診所、服務月份。

    Returns:
        {'clinic_short': '澤豐', 'yyyymm': '11503', 'service_month': '2026-03-01'}
    """
    name = Path(filename).name
    m = FILENAME_RE.match(name)
    if not m:
        raise ValueError(f"檔名格式不符健保人數&初診統計：{name}")
    yyyymm = m.group(1)
    clinic_short = m.group(2)
    roc_y, roc_m = int(yyyymm[:3]), int(yyyymm[3:])
    return {
        "clinic_short": clinic_short,
        "yyyymm": yyyymm,
        "service_month": f"{roc_y + 1911:04d}-{roc_m:02d}-01",
    }


def parse_visit_count(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    name_to_doctor_id: dict[str, int],
) -> tuple[list[dict], dict | None]:
    """
    解析一份健保人數+初診統計 xlsx。

    Args:
        file_obj: file-like
        source_filename: 原始檔名（取 service_month）
        clinic_id: clinics.id
        name_to_doctor_id: {醫師姓名: doctor_id}（呼叫端從 doctors 表查好）

    Returns:
        (doctor_records, clinic_rates)
        doctor_records: list[dict]，每位醫師對應 doctor_visit_stats 一個 row
        clinic_rates: dict 對應 clinic_visit_rates；找不到時 None

    Raises:
        ValueError: 檔名/結構錯、醫師名不在 doctors 表
    """
    meta = parse_filename(source_filename)
    service_month = meta["service_month"]

    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    # 找出醫師資料列：從 R5 起，C1 有非空姓名且不是「合計：」/「總計」
    # 醫師列範圍止於遇到「合計：」或空白
    doctor_records: list[dict] = []
    unknown_doctors: list[str] = []
    last_data_row = 4  # 用來找診所彙總起始

    for r in range(5, df.shape[0]):
        name_cell = df.iloc[r, DOCTOR_COLS["醫師姓名"]]
        if pd.isna(name_cell):
            continue
        name = str(name_cell).strip()
        if not name or "合計" in name or "總計" in name:
            last_data_row = r
            break
        if name not in name_to_doctor_id:
            unknown_doctors.append(name)
            continue

        rec = {
            "clinic_id": clinic_id,
            "doctor_id": name_to_doctor_id[name],
            "service_month": service_month,
            "nhi_internal":            _to_int(df.iloc[r, DOCTOR_COLS["內科"]]),
            "nhi_pure_acu":            _to_int(df.iloc[r, DOCTOR_COLS["純針"]]),
            "nhi_pure_trauma":         _to_int(df.iloc[r, DOCTOR_COLS["純傷"]]),
            "nhi_internal_acu":        _to_int(df.iloc[r, DOCTOR_COLS["內+針"]]),
            "nhi_internal_trauma":     _to_int(df.iloc[r, DOCTOR_COLS["內+傷"]]),
            "nhi_visits_total":        _to_int(df.iloc[r, DOCTOR_COLS["健保總數"]]),
            "cash_visits_internal":    _to_int(df.iloc[r, DOCTOR_COLS["自費內科"]]),
            "cash_visits_acupuncture": _to_int(df.iloc[r, DOCTOR_COLS["自費針傷"]]),
            "total_visits":            _to_int(df.iloc[r, DOCTOR_COLS["合計"]]),
            "acu_first_visit":         _to_int(df.iloc[r, DOCTOR_COLS["針傷科第1次"]]),
            "sessions_morning":        _to_int(df.iloc[r, DOCTOR_COLS["早"]]),
            "sessions_noon":           _to_int(df.iloc[r, DOCTOR_COLS["中"]]),
            "sessions_evening":        _to_int(df.iloc[r, DOCTOR_COLS["晚"]]),
            "sessions_total":          _to_int(df.iloc[r, DOCTOR_COLS["診數合計"]]),
        }
        doctor_records.append(rec)
        last_data_row = r

    if unknown_doctors:
        raise ValueError(
            f"{source_filename}：以下醫師不在 doctors 表：{unknown_doctors}"
        )

    # 解析診所彙總（初診/複診/自費/特約卡/掛號優免）
    # 在「合計：」列之後。每列 C0=標籤、C2=人次、C4=率名稱、C6=百分比
    clinic_rates = _parse_clinic_rates(df, last_data_row, clinic_id, service_month)

    return doctor_records, clinic_rates


def _parse_clinic_rates(
    df: pd.DataFrame,
    start_row: int,
    clinic_id: int,
    service_month: str,
) -> dict | None:
    """從醫師列之後的彙總段抓初診/複診/自費/特約卡/掛號優免"""
    rates: dict = {
        "clinic_id": clinic_id,
        "service_month": service_month,
    }
    label_field = {
        "初診人次": ("first_visit_count", "first_visit_rate"),
        "複診人次": ("revisit_count", "revisit_rate"),
        "自費人次": ("cash_visit_count", "cash_visit_rate"),
        "特約卡人次": ("contracted_card_count", "contracted_card_rate"),
        "免掛號費人次": ("free_reg_count", "free_reg_rate"),
        "優待掛號費人次": ("free_reg_count", "free_reg_rate"),  # 澤沛措辭
    }
    found_any = False
    for r in range(start_row, df.shape[0]):
        c0 = df.iloc[r, 0]
        if pd.isna(c0):
            continue
        label = str(c0).strip().rstrip("：:").strip()
        if label not in label_field:
            continue
        count_field, rate_field = label_field[label]
        rates[count_field] = _to_int(df.iloc[r, 2])
        rates[rate_field] = _to_float(df.iloc[r, 6])
        found_any = True

    return rates if found_any else None
