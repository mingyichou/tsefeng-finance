"""
醫師自費統計逐筆匯入處理（Sprint 2.6）

對應資料字典 §3.4 / schema doctor_cash_visits

檔案結構：
  R0: 機構碼 + 診所名
  R1: "115年MM月01日至XX日醫師自費業績門診統計報表(醫師名)"
  R2: 列印日期 / 列印人員
  R3: 表頭
  R4-Rn: 逐筆病患就診明細
  最後一列: C0='總計'，各欄位為加總

欄位對齊（澤豐 20 欄 / 澤沛 18 欄；C0-C17 兩家相同）：
  C0=日期 (民國 115/03/05) → visit_date
  C1=病歷號 → chart_no
  C2=姓名 → ⛔ 不寫入（隱私）
  C3=病名 → diagnosis
  C4=用藥 → prescription
  C5=醫師(銷售人) → 用來 cross-check 檔名解析的醫師
  C6=掛號費 → registration
  C7=內服藥 → internal_drug
  C8=外用藥 → external_drug
  C9=針灸費 → acupuncture
  C10=傷科費 → trauma
  C11=脫臼費 → dislocation
  C12=保養費 → wellness
  C13=飲片費 → herb_decoction
  C14=診察費 → consult
  C15=檢驗費 → lab
  C16=其它 → other
  C17=自費合計 → cash_total
  C18=地址（澤豐） → ⛔ 不寫入（隱私）
  C19=電話（澤豐） → ⛔ 不寫入（隱私）

檔名規則（多種格式並存）：
  澤豐：澤豐{醫師}醫師自費統計{YYYYMM}.xlsx
        {醫師}醫師自費統計{YYYYMM}.xlsx     (呂敏盛無「澤豐」前綴)
  澤沛：{YYYYMM}月自費-{周/胡}.xlsx          (11501)
        {YYYYMM}自費-{周/胡}.xlsx            (11502+)
        澤沛{醫師}醫師自費統計{YYYYMM}.xlsx (相容)

⚠️ 隱私：姓名/地址/電話一律不寫入 DB
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import IO

import pandas as pd


CASH_COLS = {
    "日期": 0, "病歷號": 1, "姓名": 2, "病名": 3, "用藥": 4, "醫師": 5,
    "掛號費": 6, "內服藥": 7, "外用藥": 8, "針灸費": 9, "傷科費": 10,
    "脫臼費": 11, "保養費": 12, "飲片費": 13, "診察費": 14, "檢驗費": 15,
    "其它": 16, "自費合計": 17,
}


# 抽成欄位（用於 totals 與 hash）
COMMISSION_FIELDS = [
    "registration", "internal_drug", "external_drug", "acupuncture", "trauma",
    "dislocation", "wellness", "herb_decoction", "consult", "lab", "other",
]


_DOCTOR_SHORTHAND = {"周": "周明毅", "胡": "胡舒婷", "呂": "呂敏盛"}


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
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _roc_to_iso(s: str) -> str | None:
    """115/03/05 → 2026-03-05"""
    if not s or pd.isna(s):
        return None
    s = str(s).strip()
    m = re.match(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", s)
    if not m:
        return None
    yr = int(m.group(1)) + 1911
    return f"{yr:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def parse_filename(filename: str) -> dict:
    """
    從檔名解析醫師、服務月份。

    Returns:
        {'doctor': '周明毅', 'yyyymm': '11503', 'service_month': '2026-03-01'}

    Raises:
        ValueError: 檔名格式無法識別
    """
    name = Path(filename).stem  # 去 .xlsx

    # 模式 1: 澤沛短名 — 11501月自費-周 / 11503自費-胡
    m = re.match(r"^(\d{5})(?:月)?自費-(周|胡|呂)$", name)
    if m:
        yyyymm = m.group(1)
        doctor = _DOCTOR_SHORTHAND[m.group(2)]
        return _build_meta(doctor, yyyymm)

    # 模式 2: 完整名 — 澤豐周明毅醫師自費統計11503 / 周明毅醫師自費統計11503 / 澤沛胡舒婷醫師自費統計11503
    m = re.match(r"^(?:澤豐|澤沛)?(周明毅|呂敏盛|胡舒婷)醫師自費統計(\d{5})$", name)
    if m:
        return _build_meta(m.group(1), m.group(2))

    # 模式 3: 11410短名 — 11410周醫師自費統計
    m = re.match(r"^(\d{5})(周|胡|呂)醫師自費統計$", name)
    if m:
        return _build_meta(_DOCTOR_SHORTHAND[m.group(2)], m.group(1))

    raise ValueError(f"檔名格式不符自費統計規則：{name}")


def _build_meta(doctor: str, yyyymm: str) -> dict:
    roc_y, roc_m = int(yyyymm[:3]), int(yyyymm[3:])
    service_month = f"{roc_y + 1911:04d}-{roc_m:02d}-01"
    return {"doctor": doctor, "yyyymm": yyyymm, "service_month": service_month}


def _row_hash(rec: dict) -> str:
    """用 (clinic_id, doctor_id, visit_date, chart_no, 各金額) 雜湊防重複匯入"""
    parts = [
        str(rec.get("clinic_id") or ""),
        str(rec.get("doctor_id") or ""),
        str(rec.get("visit_date") or ""),
        str(rec.get("chart_no") or ""),
        str(rec.get("cash_total") or ""),
    ]
    parts.extend(str(rec.get(f) or 0) for f in COMMISSION_FIELDS)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def parse_cash_visits(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    doctor_id: int,
    expected_doctor_name: str | None = None,
) -> tuple[list[dict], dict]:
    """
    解析一份醫師自費統計 xlsx，回傳 (records, totals_check)。

    Args:
        file_obj: file-like (Streamlit uploader)
        source_filename: 原始檔名（diagnostic）
        clinic_id: clinics.id
        doctor_id: doctors.id（呼叫端依檔名解析）
        expected_doctor_name: （選填）若提供，驗證 C5 是否一致

    Returns:
        records: list[dict]，每筆對應 doctor_cash_visits 一個 row
                  （不含姓名/地址/電話）
        totals_check: {
          'parsed_total': SUM 各項目,
          'file_total':   檔案最後「總計」列的 cash_total,
          'parsed_count': len(records),
          'file_count':   檔案總計列 chart_no 欄位（記載筆數）,
          'matches':      bool（金額與筆數是否相符）,
        }

    Raises:
        ValueError: 檔案結構錯、找不到表頭/總計列、醫師不符
    """
    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    # 找「總計」列
    total_row_idx = None
    for r in range(df.shape[0]):
        v = df.iloc[r, 0]
        if pd.notna(v) and "總計" in str(v):
            total_row_idx = r
            break
    if total_row_idx is None:
        raise ValueError(f"{source_filename}：找不到「總計」列")

    records = []
    seen_doctors = set()

    # 資料列從 R4 到 total_row_idx-1
    for r in range(4, total_row_idx):
        date_iso = _roc_to_iso(df.iloc[r, CASH_COLS["日期"]])
        if not date_iso:
            continue  # 空白列或無效日期，略過
        chart_no = _to_int(df.iloc[r, CASH_COLS["病歷號"]])  # 0 也允許

        c5 = df.iloc[r, CASH_COLS["醫師"]]
        if pd.notna(c5):
            seen_doctors.add(str(c5).strip())

        rec = {
            "clinic_id": clinic_id,
            "doctor_id": doctor_id,
            "visit_date": date_iso,
            "chart_no": chart_no,
            "diagnosis": _str_or_none(df.iloc[r, CASH_COLS["病名"]]),
            "prescription": _str_or_none(df.iloc[r, CASH_COLS["用藥"]]),
            "registration":   _to_int(df.iloc[r, CASH_COLS["掛號費"]]),
            "internal_drug":  _to_int(df.iloc[r, CASH_COLS["內服藥"]]),
            "external_drug":  _to_int(df.iloc[r, CASH_COLS["外用藥"]]),
            "acupuncture":    _to_int(df.iloc[r, CASH_COLS["針灸費"]]),
            "trauma":         _to_int(df.iloc[r, CASH_COLS["傷科費"]]),
            "dislocation":    _to_int(df.iloc[r, CASH_COLS["脫臼費"]]),
            "wellness":       _to_int(df.iloc[r, CASH_COLS["保養費"]]),
            "herb_decoction": _to_int(df.iloc[r, CASH_COLS["飲片費"]]),
            "consult":        _to_int(df.iloc[r, CASH_COLS["診察費"]]),
            "lab":            _to_int(df.iloc[r, CASH_COLS["檢驗費"]]),
            "other":          _to_int(df.iloc[r, CASH_COLS["其它"]]),
            "cash_total":     _to_int(df.iloc[r, CASH_COLS["自費合計"]]),
        }
        rec["raw_row_hash"] = _row_hash(rec)
        records.append(rec)

    # 醫師一致性檢查
    if expected_doctor_name and seen_doctors:
        unexpected = seen_doctors - {expected_doctor_name}
        if unexpected:
            raise ValueError(
                f"{source_filename}：檔內醫師欄位含 {unexpected}，"
                f"但檔名指向 {expected_doctor_name}"
            )

    # 總計驗證
    # ⚠️ 檔案「總計列」自費合計 C17 不含掛號費（C6 標「不計」或空）
    # 所以對帳邏輯：除 registration 外 9 欄 SUM 對齊；cash_total = 逐筆 SUM − registration
    total_row = df.iloc[total_row_idx]
    file_total = _to_int(total_row[CASH_COLS["自費合計"]])

    parsed_total_raw = sum(r["cash_total"] for r in records)
    parsed_total_excl_reg = parsed_total_raw - sum(r["registration"] for r in records)

    by_field = {f: sum(r[f] for r in records) for f in COMMISSION_FIELDS}
    file_by_field = {
        "registration":   _to_int(total_row[CASH_COLS["掛號費"]]),  # 通常 0/'不計'
        "internal_drug":  _to_int(total_row[CASH_COLS["內服藥"]]),
        "external_drug":  _to_int(total_row[CASH_COLS["外用藥"]]),
        "acupuncture":    _to_int(total_row[CASH_COLS["針灸費"]]),
        "trauma":         _to_int(total_row[CASH_COLS["傷科費"]]),
        "dislocation":    _to_int(total_row[CASH_COLS["脫臼費"]]),
        "wellness":       _to_int(total_row[CASH_COLS["保養費"]]),
        "herb_decoction": _to_int(total_row[CASH_COLS["飲片費"]]),
        "consult":        _to_int(total_row[CASH_COLS["診察費"]]),
        "lab":            _to_int(total_row[CASH_COLS["檢驗費"]]),
        "other":          _to_int(total_row[CASH_COLS["其它"]]),
    }
    # 對帳：欄位差異（除 registration / consult — 部分檔案總計列空白）
    # 兩家檔案行為不同：
    #   澤沛：總計 C17 = 各項目 SUM 不含掛號費（C6 標「不計」）
    #   澤豐：總計 C17 = 含掛號費的逐筆 cash_total SUM（C6 空白）
    # 所以「合計對帳」只要 raw 或 excl_reg 任一等於 file_total 就視為一致
    field_diffs = {
        f: (by_field[f], file_by_field[f])
        for f in COMMISSION_FIELDS
        if f != "registration" and by_field[f] != file_by_field[f]
    }
    cash_total_match = (
        parsed_total_excl_reg == file_total or parsed_total_raw == file_total
    )
    totals_inclusive_reg = parsed_total_raw == file_total  # 澤豐風格
    totals_exclusive_reg = parsed_total_excl_reg == file_total  # 澤沛風格

    totals = {
        "parsed_total_raw": parsed_total_raw,
        "parsed_total_excl_reg": parsed_total_excl_reg,
        "file_total": file_total,
        "parsed_count": len(records),
        "registration_handling": (
            "含掛號" if totals_inclusive_reg else
            "不含掛號" if totals_exclusive_reg else "對不上"
        ),
        "by_field": by_field,
        "file_by_field": file_by_field,
        "field_diffs": field_diffs,
        "cash_total_match": cash_total_match,
        "matches": cash_total_match,
    }
    return records, totals


def _str_or_none(v) -> str | None:
    if pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None
