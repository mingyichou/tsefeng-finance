"""
診所支出類匯入處理（Sprint 2.7a：現金 + 合約）

對應 schema:
  - cash_expense       (兩家共用) 一筆一列
  - contract_expense   (兩家共用) 橫向月度表 → 轉長表

(支票支出 check_expense + 調貨 inventory_transfer 留 Sprint 2.7b)
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import IO

import pandas as pd


# ─── 現金支出 ─────────────────────────────────────────────


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
        s = v.strip().replace(",", "")
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


def _norm_str(v) -> str | None:
    if pd.isna(v):
        return None
    s = str(v).strip()
    return s if s else None


def _row_hash_cash(rec: dict) -> str:
    parts = [
        str(rec.get("clinic_id") or ""),
        str(rec.get("expense_date") or ""),
        str(rec.get("amount") or ""),
        str(rec.get("description") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def parse_cash_expense(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    roc_year: int = 115,
) -> list[dict]:
    """
    解析現金支出 xlsx。

    結構：
      R0: 表頭（C0=年, C2-3=描述+支出, 末欄=備註）
      R1-Rn: C0=月, C1=日(可能小數如 5.0), C2=描述, C3=金額, C末=備註(可選)

    Args:
        roc_year: 民國年（檔名通常是「澤豐中醫診所現金支出.xlsx」沒帶年；
                  預設 115，可由 UI 讓使用者選）
    """
    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    # 找出備註欄（最後有「備註」標題的欄）
    note_col = None
    if df.shape[0] > 0:
        for c in range(df.shape[1] - 1, -1, -1):
            v = df.iloc[0, c]
            if pd.notna(v) and "備註" in str(v):
                note_col = c
                break

    records: list[dict] = []
    for r in range(1, df.shape[0]):
        month = _to_int(df.iloc[r, 0])
        day = _to_int(df.iloc[r, 1])
        desc = _norm_str(df.iloc[r, 2])
        amount = _to_int(df.iloc[r, 3])
        if not (month and day and desc and amount):
            continue
        if not (1 <= month <= 12) or not (1 <= day <= 31):
            continue
        ad_y = roc_year + 1911
        try:
            expense_date = f"{ad_y:04d}-{month:02d}-{day:02d}"
            # 驗證日期合法
            pd.Timestamp(expense_date)
        except Exception:
            continue
        accrual_month = f"{ad_y:04d}-{month:02d}-01"
        note = _norm_str(df.iloc[r, note_col]) if note_col is not None else None

        rec = {
            "clinic_id": clinic_id,
            "expense_date": expense_date,
            "description": desc,
            "amount": amount,
            "note": note,
            "accrual_month": accrual_month,
        }
        rec["raw_row_hash"] = _row_hash_cash(rec)
        records.append(rec)

    return records


# ─── 合約支出（橫向月度表 → 長表）─────────────────────


def parse_contract_expense(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
) -> list[dict]:
    """
    解析合約支出 xlsx（橫向月度表）。

    結構（澤豐）：
      R0: 表頭（廠商名稱列）
      R1+: 列 = YYYYMM (民國)，欄 = 各廠商金額
      最後若干欄是「月總」「年總支出」等彙總欄，需略過
      可能有「房租(玉)」這類含括號的廠商名

    結構（澤沛）：
      多段表頭（R1=備註行 R2=廠商行；115年表頭在 R17/R18）
      可能含 114年下半年資料

    策略：
      1. 找所有看起來是月份的列（C0 形如 11501-11512 或 11401-11412）
      2. 由該列向上找最近一個「廠商表頭」列（含「簽口」/「叫貨」/「房租」等）
      3. 對每個 (月份, 廠商) cell 抓金額；月總欄略過
    """
    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    # 找出所有月份列（C0 像 11501）
    month_pattern = re.compile(r"^(\d{3})(\d{2})\.?\d*$")  # 11501 或 11501.0
    month_rows: list[tuple[int, str]] = []
    for r in range(df.shape[0]):
        v = df.iloc[r, 0]
        if pd.isna(v):
            continue
        s = str(v).strip()
        m = month_pattern.match(s)
        if m:
            roc_y = int(m.group(1))
            mo = int(m.group(2))
            if 1 <= mo <= 12 and 100 <= roc_y <= 130:
                ad_y = roc_y + 1911
                month_rows.append((r, f"{ad_y:04d}-{mo:02d}-01"))

    # 對每個月份列，找該列上方最近一個「廠商表頭列」
    # 廠商列特徵：欄位含「簽口」、「叫貨」、「房租」、「合約」、「應收帳款」
    vendor_keywords = ("簽口", "叫貨", "房租", "合約", "通知書", "通知單", "明細")

    def find_vendor_header(row_idx: int) -> dict[int, str] | None:
        """從 row_idx 往上找含「簽口」「房租」等關鍵字的列，回傳 {col: vendor_name}"""
        for r in range(row_idx - 1, -1, -1):
            cells = df.iloc[r]
            keyword_hits = sum(
                1 for v in cells
                if pd.notna(v) and any(k in str(v) for k in vendor_keywords)
            )
            if keyword_hits >= 2:
                vendors = {}
                for c, v in enumerate(cells):
                    if pd.isna(v):
                        continue
                    name = str(v).strip()
                    if not name:
                        continue
                    # 排除「月總/年總」彙總欄
                    if any(skip in name for skip in (
                        "月總", "年總", "年平均", "支出", "記帳方式"
                    )):
                        continue
                    # 排除「電子發票」「收款單」等備註行（通常含 *0.93 等）
                    if "*" in name or name.startswith("11"):
                        continue
                    # 留下含關鍵字的合法廠商
                    if any(k in name for k in vendor_keywords) or len(name) <= 12:
                        vendors[c] = name
                return vendors
        return None

    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row_idx, service_month in month_rows:
        vendor_map = find_vendor_header(row_idx)
        if not vendor_map:
            continue
        for col, vendor in vendor_map.items():
            if col >= df.shape[1]:
                continue
            val = df.iloc[row_idx, col]
            if pd.isna(val):
                continue
            amount = _to_float(val)
            if amount is None or amount == 0:
                continue
            key = (service_month, vendor)
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "clinic_id": clinic_id,
                "service_month": service_month,
                "vendor": vendor,
                "amount": round(amount, 2),
                "note": None,
            })

    return records
