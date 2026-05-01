"""
自費商品成本&售價 匯入處理（含「膠囊&OTC」+「自費藥粉&自費商品」雙 sheet）

對應 schema product_pricing
  - effective_month / vendor / product_name / cost_price / sale_price / unit / note

兩個 sheet：
  1. 「膠囊&OTC」(7 欄) — 進價 vs 售價對照
     vendor 識別：(xxx) 括號 / 分類標題（自費處方/保健食品）
  2. 「自費藥粉&自費商品」(11 欄) — 雙塊結構
     左塊 C0-C5：品項 / 廠商 / 進價 / 單價(g/元) / 售價(g) / 備註
     右塊 C8-C10：品項 / 單價 / 備註（vendor 固定為大墩）
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


# ─── 自費藥粉&自費商品 sheet ─────────────────────────


# 此 sheet 內常見的非品項標題列關鍵字
_POWDER_SKIP_KEYWORDS = (
    "自費診限定", "自費處方", "保健食品",
)


def parse_self_pay_powder(
    file_obj: IO,
    source_filename: str,
    effective_month: str,
) -> list[dict]:
    """
    解析「自費藥粉&自費商品」sheet（雙塊結構）：
      左塊 C0-C5：品項 / 廠商 / 進價 / 單價(g/元) / 售價(g) / 備註
      右塊 C8-C10：品項 / 單價 / 備註（vendor=大墩）
    """
    file_obj.seek(0)
    try:
        df = pd.read_excel(file_obj, sheet_name="自費藥粉&自費商品", header=None)
    except ValueError:
        return []  # 該檔沒此 sheet（容錯）

    records: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # ─── 左塊 ───
    for r in range(1, df.shape[0]):
        product = _norm_str(df.iloc[r, 0])
        if not product:
            continue
        # 跳過分類標題與日期分段
        if any(k in product for k in _POWDER_SKIP_KEYWORDS):
            continue
        if re.match(r"^\d{2,3}/\d{1,2}$", product):
            continue

        vendor = _norm_str(df.iloc[r, 1]) if df.shape[1] > 1 else None
        if not vendor:
            continue

        cost = _to_float(df.iloc[r, 2]) if df.shape[1] > 2 else None
        # C3 = 單價(g/元)、C4 = 售價(g)；以 C4 售價優先
        sale_g = _to_float(df.iloc[r, 4]) if df.shape[1] > 4 else None
        if sale_g is None and df.shape[1] > 3:
            sale_g = _to_float(df.iloc[r, 3])
        note = _norm_str(df.iloc[r, 5]) if df.shape[1] > 5 else None

        if cost is None and sale_g is None:
            continue

        key = (vendor, product)
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "effective_month": effective_month,
            "vendor": vendor,
            "product_name": product,
            "cost_price": round(cost, 2) if cost is not None else None,
            "sale_price": round(sale_g, 2) if sale_g is not None else None,
            "unit": "g",
            "note": note,
        })

    # ─── 右塊（C8-C10）vendor=大墩 ───
    if df.shape[1] > 8:
        for r in range(1, df.shape[0]):
            product = _norm_str(df.iloc[r, 8])
            if not product:
                continue
            if any(k in product for k in _POWDER_SKIP_KEYWORDS):
                continue
            sale = _to_float(df.iloc[r, 9]) if df.shape[1] > 9 else None
            note = _norm_str(df.iloc[r, 10]) if df.shape[1] > 10 else None
            if sale is None:
                continue
            key = ("大墩", product)
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "effective_month": effective_month,
                "vendor": "大墩",
                "product_name": product,
                "cost_price": None,
                "sale_price": round(sale, 2),
                "unit": None,
                "note": note,
            })

    return records


def parse_self_pay_all_sheets(
    file_obj: IO,
    source_filename: str,
    effective_month: str,
) -> list[dict]:
    """
    解析自費商品檔的兩個主要 sheet 並合併：
      - 「膠囊&OTC」
      - 「自費藥粉&自費商品」
    若同一 (vendor, product_name) 出現兩次，OTC 優先（先 parse）。
    """
    otc = parse_self_pay_otc(file_obj, source_filename, effective_month)
    powder = parse_self_pay_powder(file_obj, source_filename, effective_month)
    seen: set[tuple[str, str]] = {(r["vendor"], r["product_name"]) for r in otc}
    out = list(otc)
    for r in powder:
        key = (r["vendor"], r["product_name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
