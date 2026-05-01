"""
自費商品成本&售價 匯入處理（Sprint 2.8a 部分）

對應 schema product_pricing
  - effective_month / vendor / product_name / cost_price / sale_price / unit / note

主要解析 sheet「膠囊&OTC」（最直接的「進價 vs 售價」對照表）：
  R0: 表頭（廠商/品項/單位/進價/價格/原價格/備註）
  vendor 識別：
    - C0 出現「(xxx)」格式 → vendor = xxx（影響後續列直到下一個 vendor）
    - C0 出現「自費處方」「保健食品」等分類標題 → 視為 vendor

⚠️ sheets「自費藥粉&自費商品」與「自費商品單價-金流計算」結構不同，
   暫由 Sprint 2.8b 補完。
"""

from __future__ import annotations

import re
from typing import IO

import pandas as pd


# 「膠囊&OTC」表頭欄位
OTC_COLS = {
    "廠商": 0,
    "品項": 1,
    "單位": 2,
    "進價": 3,
    "價格": 4,
    "原價格": 5,
    "備註": 6,
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


# 「(廠商名)」格式
_VENDOR_PAREN_RE = re.compile(r"^\(([^)]+)\)$")


def parse_self_pay_otc(
    file_obj: IO,
    source_filename: str,
    effective_month: str,
) -> list[dict]:
    """
    解析自費商品「膠囊&OTC」sheet。

    Args:
        effective_month: 'YYYY-MM-01' 生效月（由 UI 指定，因檔內每品項共用）

    Returns:
        list[dict] 對應 product_pricing 的 rows
    """
    file_obj.seek(0)
    try:
        df = pd.read_excel(file_obj, sheet_name="膠囊&OTC", header=None)
    except ValueError:
        # 找不到該 sheet，試 sheet_name=0（第一個）
        file_obj.seek(0)
        df = pd.read_excel(file_obj, sheet_name=0, header=None)

    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    current_vendor: str | None = None

    for r in range(1, df.shape[0]):
        c0 = _norm_str(df.iloc[r, OTC_COLS["廠商"]])
        c1 = _norm_str(df.iloc[r, OTC_COLS["品項"]])
        c2 = _norm_str(df.iloc[r, OTC_COLS["單位"]])
        cost = _to_float(df.iloc[r, OTC_COLS["進價"]])
        sale = _to_float(df.iloc[r, OTC_COLS["價格"]])
        note = _norm_str(df.iloc[r, OTC_COLS["備註"]]) if df.shape[1] > 6 else None

        # vendor 切換規則：
        #   c0='(xxx)' 括號格式 → vendor=xxx（同列若有品項繼續收）
        #   c0='自費處方'分類標題 → vendor=自費處方（同列若有品項繼續收）
        if c0:
            m = _VENDOR_PAREN_RE.match(c0)
            if m:
                current_vendor = m.group(1).strip()
            else:
                current_vendor = c0
            # 同列若沒品項，純分類標題列，跳過收錄
            if not c1:
                continue

        # 必須有品項 + vendor
        if not c1 or not current_vendor:
            continue

        # 至少要有一個價格資訊才收
        if cost is None and sale is None:
            continue

        key = (current_vendor, c1)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "effective_month": effective_month,
            "vendor": current_vendor,
            "product_name": c1,
            "cost_price": round(cost, 2) if cost is not None else None,
            "sale_price": round(sale, 2) if sale is not None else None,
            "unit": c2,
            "note": note,
        })

    return records
