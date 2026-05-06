"""
科中進貨價目表 匯入處理（澤豐澤沛金流結算用）

對應 schema tcm_concentrate_pricing
  - id / category / product_name / vendor / price / effective_month / source_filename / uploaded_at

來源檔：@科中進貨價目表.xlsx（共 4 sheets，僅取以下 2 個）：
  - 「科學中藥-複」  (category='複方')
  - 「科學中藥-單方」(category='單方')

格式：
  R0: 標題或日期
  R1: 'X方' (=複方/單方)
  R2: 廠商列  C2='天一' C3='港香蘭' C4='莊松榮' C5='科達' C6='順天堂' C7='順天堂K裝/莊無'
  R3: '品項' label
  R4+: data
       C0='ㄅ' (注音標籤可選)
       C1='品項'
       C2..C7=各廠商價格

注意：
  - 順天堂K裝（單位較大如1KG桶）/莊無 不參與計算 → 略過
  - 院長指定的計算廠商：天一/港香蘭/莊松榮/科達/順天堂/仙豐
  - 仙豐目前不在表中，但保留辨識（將來補表用）
"""

from __future__ import annotations

import re
from typing import IO

import pandas as pd


# 計算用的廠商白名單（其它欄位即使有也不寫入）
ALLOWED_VENDORS = {"天一", "港香蘭", "莊松榮", "科達", "順天堂", "仙豐"}

# 兩個目標 sheet 名稱（可變體：「科學中藥-複」或「科學中藥-複方」）
SHEET_NAME_PATTERNS = {
    "複方": [r"^科學中藥[\-－]複(方)?$"],
    "單方": [r"^科學中藥[\-－]單方$"],
}


def _to_float(v) -> float | None:
    if pd.isna(v):
        return None
    if isinstance(v, str):
        s = v.replace(",", "").strip()
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


def _find_sheet(xl: pd.ExcelFile, category: str) -> str | None:
    patterns = SHEET_NAME_PATTERNS.get(category, [])
    for sn in xl.sheet_names:
        for pat in patterns:
            if re.match(pat, sn):
                return sn
    return None


def _parse_one_sheet(
    df: pd.DataFrame,
    category: str,
) -> list[dict]:
    """
    解析單張 sheet。回傳 list of (category, vendor, product_name, price)。
    定位廠商列：找含至少 2 個 ALLOWED_VENDORS 的列。
    """
    # ─── Step 1: 找廠商列 ───
    vendor_row_idx = None
    vendor_cols: dict[int, str] = {}  # col_idx -> vendor_name

    for r in range(min(10, df.shape[0])):
        col_to_v: dict[int, str] = {}
        for c in range(df.shape[1]):
            v = _norm_str(df.iloc[r, c])
            if v and v in ALLOWED_VENDORS:
                col_to_v[c] = v
        if len(col_to_v) >= 2:
            vendor_row_idx = r
            vendor_cols = col_to_v
            break

    if vendor_row_idx is None or not vendor_cols:
        return []

    # ─── Step 2: 從 vendor_row 之後逐列抓品項+價格 ───
    records: list[dict] = []
    for r in range(vendor_row_idx + 1, df.shape[0]):
        # C1 為品項；C0 為注音標籤略過
        product = _norm_str(df.iloc[r, 1]) if df.shape[1] > 1 else None
        if not product:
            continue
        # 跳過表頭/分區列
        if product in ("品項", "複方", "單方"):
            continue

        for c, vendor in vendor_cols.items():
            price = _to_float(df.iloc[r, c]) if c < df.shape[1] else None
            if price is None or price <= 0:
                continue
            records.append({
                "category": category,
                "product_name": product,
                "vendor": vendor,
                "price": round(price, 2),
            })

    return records


def parse_tcm_concentrate(
    file_obj: IO,
    source_filename: str,
    effective_month: str,
) -> list[dict]:
    """
    解析「@科中進貨價目表.xlsx」→ 兩個 sheet 合併。

    Args:
        effective_month: 'YYYY-MM-01'（檔案無顯式月份；由 UI 帶入）

    Returns:
        list[dict] 對應 tcm_concentrate_pricing：
          category, product_name, vendor, price, effective_month, source_filename
    """
    file_obj.seek(0)
    xl = pd.ExcelFile(file_obj)

    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()  # (category, vendor, product_name)

    for category in ("複方", "單方"):
        sn = _find_sheet(xl, category)
        if not sn:
            continue
        file_obj.seek(0)
        df = pd.read_excel(file_obj, sheet_name=sn, header=None)
        for rec in _parse_one_sheet(df, category):
            key = (rec["category"], rec["vendor"], rec["product_name"])
            if key in seen:
                continue
            seen.add(key)
            rec["effective_month"] = effective_month
            rec["source_filename"] = source_filename
            out.append(rec)

    return out
