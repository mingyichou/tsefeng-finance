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


# ─── 自費藥粉&自費商品 sheet（11504 新格式）─────────────
#
# 新格式（院長 2026-05-07 調整）：
#   上區 (R0-R9)：C0=品項, C1=廠商, C2=進價, C3=售價/G
#   下方廠商區塊：
#     R(n)   C0='大墩' / '駿賀' / '上賀' / '怡得' / '港香蘭' / '卡媚迪斯' / '羿嘉' / '水藥包'
#     R(n+1) C1='進價'  (有時 C0 與 C1='進價' 同列)
#     R(n+2..) C0=品項, C1=進價數值, C2=備註(可選)


_POWDER_SKIP_KEYWORDS = (
    "自費診限定", "自費處方", "保健食品",
)


def parse_self_pay_powder(
    file_obj: IO,
    source_filename: str,
    effective_month: str,
) -> list[dict]:
    """
    解析「自費藥粉&自費商品」sheet（新格式 2026-05）：
      上區 C0-C3：品項 / 廠商 / 進價 / 售價(g)
      下方廠商區塊：C0=品項, C1=進價, C2=備註(可選)；vendor 由區塊標題決定。
    """
    file_obj.seek(0)
    try:
        df = pd.read_excel(file_obj, sheet_name="自費藥粉&自費商品", header=None)
    except ValueError:
        return []

    records: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # 第 0 列為表頭（C1='廠商' C2='進價' C3='售價/G'）；定位上區範圍 = 上區 + 下區的轉折
    # 轉折判斷：第一個「整列 C0=text 但 C1 為空（廠商區塊標題）」之前都當上區
    transition_row = df.shape[0]
    for r in range(1, df.shape[0]):
        c0 = _norm_str(df.iloc[r, 0])
        c1 = _norm_str(df.iloc[r, 1]) if df.shape[1] > 1 else None
        c2 = _norm_str(df.iloc[r, 2]) if df.shape[1] > 2 else None
        # 「廠商區塊標題列」格式：C0 有非數字 text + C1 空 + C2 空
        # 或同列宣告: C0=vendor + C1='進價'
        is_vendor_block_header = bool(
            c0 and not c1 and not c2 and not _to_float(df.iloc[r, 0])
        ) or (c0 and c1 == "進價")
        if is_vendor_block_header:
            transition_row = r
            break

    # ─── 上區：C0=品項, C1=廠商, C2=進價, C3=售價/G ───
    for r in range(1, transition_row):
        product = _norm_str(df.iloc[r, 0])
        if not product:
            continue
        if any(k in product for k in _POWDER_SKIP_KEYWORDS):
            continue
        if re.match(r"^\d{2,3}/\d{1,2}$", product):
            continue

        vendor = _norm_str(df.iloc[r, 1]) if df.shape[1] > 1 else None
        if not vendor:
            continue

        cost = _to_float(df.iloc[r, 2]) if df.shape[1] > 2 else None
        sale_g = _to_float(df.iloc[r, 3]) if df.shape[1] > 3 else None

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
            "note": None,
        })

    # ─── 下方廠商區塊 ───
    # 狀態機：current_block_vendor + in_block
    current_block_vendor: str | None = None
    in_block = False

    r = transition_row
    while r < df.shape[0]:
        c0 = _norm_str(df.iloc[r, 0])
        c1_raw = df.iloc[r, 1] if df.shape[1] > 1 else None
        c1 = _norm_str(c1_raw)
        c2 = _norm_str(df.iloc[r, 2]) if df.shape[1] > 2 else None

        # 偵測「同列宣告」：C0=vendor + C1='進價'
        if c0 and c1 == "進價" and not _to_float(df.iloc[r, 0]):
            current_block_vendor = c0
            in_block = True
            r += 1
            continue

        # 偵測「分列宣告」：C0=vendor + C1/C2 空 + 下一列 C1='進價'
        if c0 and not c1 and not c2 and not _to_float(df.iloc[r, 0]):
            next_c1 = (
                _norm_str(df.iloc[r + 1, 1])
                if r + 1 < df.shape[0] and df.shape[1] > 1
                else None
            )
            if next_c1 == "進價":
                current_block_vendor = c0
                in_block = False  # 等下一列的進價標頭啟動
                r += 1
                continue

        # 進價標頭列（C0 空 + C1='進價'）：啟動 block
        if not c0 and c1 == "進價":
            in_block = True
            r += 1
            continue

        # 區塊內品項列：C0=品項, C1=進價數值
        if in_block and current_block_vendor and c0:
            cost = _to_float(c1_raw)
            if cost is None:
                # 無價格列（可能備註）— 跳過但不終止 block
                r += 1
                continue
            note = c2
            key = (current_block_vendor, c0)
            if key in seen:
                r += 1
                continue
            seen.add(key)
            records.append({
                "effective_month": effective_month,
                "vendor": current_block_vendor,
                "product_name": c0,
                "cost_price": round(cost, 2),
                "sale_price": None,
                "unit": None,
                "note": note,
            })

        r += 1

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
