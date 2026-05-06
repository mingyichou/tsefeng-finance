"""
調貨整理金額試算引擎（澤豐澤沛金流結算用）

僅試算，不寫回 inventory_transfer.amount。
讀 inventory_transfer + product_pricing + tcm_concentrate_pricing，組出金流明細。

辨識邏輯：
  1. 品項名含 (天一|港香蘭|莊松榮|科達|順天堂|仙豐) 之一（括號或 dash 接續）
     → 走 tcm_concentrate_pricing：amount = qty × price × ratio
       ratio = 0.65 (天一/港香蘭) 或 0.70 (莊松榮/科達/順天堂/仙豐)
  2. 不含廠商 keyword
     → 走 product_pricing.cost_price (vendor 寬鬆比對)：amount = qty × cost_price
  3. 兩處都查不到
     → 標記 unmatched，amount=None

只返回試算結果；UI 自行決定如何呈現。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 廠商關鍵字 → 比例
BRAND_RATIO = {
    "天一": 0.65,
    "港香蘭": 0.65,
    "莊松榮": 0.70,
    "科達": 0.70,
    "順天堂": 0.70,
    "仙豐": 0.70,
}

# 廠商辨識：(廠商) 或 -廠商 或 _廠商
_BRAND_RE = re.compile(
    r"[\(\-_（](" + "|".join(BRAND_RATIO.keys()) + r")[\)）]?"
)


@dataclass
class PricedItem:
    transfer_month: str           # YYYY-MM-01
    direction: str                # "沛PAY豐" or "豐PAY沛"
    from_clinic_id: int
    to_clinic_id: int
    item: str                     # 原始品項名（含廠商標註）
    item_clean: str               # 去除廠商註後的純品項
    qty: float
    vendor: str | None            # 解出的廠商；None=無
    unit_price: float | None      # 單位進價
    ratio: float | None           # 0.65/0.70/None
    amount: float | None          # qty × unit_price × (ratio or 1)
    source: str                   # "科中複方"/"科中單方"/"自費"/"未匹配"
    note: str | None = None


def _detect_brand(item_name: str) -> tuple[str | None, str]:
    """
    從品項名抓出廠商 + 去除廠商註後的純品項名。
    如「甘麥大棗湯(科達)」 → ("科達", "甘麥大棗湯")
       「加味逍遙散-港香蘭」 → ("港香蘭", "加味逍遙散")
       「護膝」 → (None, "護膝")
    """
    m = _BRAND_RE.search(item_name)
    if not m:
        return None, item_name
    vendor = m.group(1)
    clean = (item_name[:m.start()] + item_name[m.end():]).strip(" -()（）_")
    return vendor, clean


def _build_tcm_index(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """
    tcm_concentrate_pricing → {(vendor, product_name): row}
    同 (vendor, product_name) 同時在複方/單方出現時，以複方優先。
    """
    out: dict[tuple[str, str], dict] = {}
    # 先放「複方」，再放「單方」（不蓋過已存在）
    for cat in ("複方", "單方"):
        for r in rows:
            if r.get("category") != cat:
                continue
            key = (r["vendor"], r["product_name"])
            if key in out:
                continue
            out[key] = r
    return out


def _build_pricing_index(rows: list[dict]) -> dict[str, dict]:
    """
    product_pricing → {product_name: row}（不分廠商；同 product_name 取 cost_price 非 NULL 者）
    """
    out: dict[str, dict] = {}
    for r in rows:
        name = r.get("product_name")
        if not name:
            continue
        if r.get("cost_price") is None:
            # 沒進價的略過
            continue
        if name in out:
            continue
        out[name] = r
    return out


def compute_inventory_amounts(
    inventory_rows: list[dict],
    tcm_pricing_rows: list[dict],
    product_pricing_rows: list[dict],
    fz_id: int,
    fp_id: int,
) -> list[PricedItem]:
    """
    inventory_rows: list of inventory_transfer rows (transfer_month, from_clinic_id, to_clinic_id, item, qty)
    tcm_pricing_rows: list of tcm_concentrate_pricing rows (category, vendor, product_name, price)
    product_pricing_rows: list of product_pricing rows (vendor, product_name, cost_price, ...)

    Returns: list[PricedItem]（順序同 inventory_rows）
    """
    tcm_idx = _build_tcm_index(tcm_pricing_rows)
    pp_idx = _build_pricing_index(product_pricing_rows)

    out: list[PricedItem] = []
    for row in inventory_rows:
        item = row["item"]
        qty = float(row.get("qty") or 0)
        from_id = row["from_clinic_id"]
        to_id = row["to_clinic_id"]

        # 方向：from=澤豐→to=澤沛 ⇒ 澤沛 PAY 澤豐
        if from_id == fz_id and to_id == fp_id:
            direction = "沛PAY豐"
        elif from_id == fp_id and to_id == fz_id:
            direction = "豐PAY沛"
        else:
            direction = "未知"

        vendor, clean = _detect_brand(item)

        unit_price: float | None = None
        ratio: float | None = None
        amount: float | None = None
        source = "未匹配"
        note: str | None = None

        if vendor:
            # 走科中
            tcm = tcm_idx.get((vendor, clean))
            if tcm:
                unit_price = float(tcm["price"])
                ratio = BRAND_RATIO[vendor]
                amount = round(qty * unit_price * ratio, 2)
                source = f"科中{tcm['category']}"
            else:
                source = "未匹配"
                note = f"科中表查無 {vendor} × {clean}"
        else:
            # 走自費商品（不分廠商，僅靠 product_name）
            pp = pp_idx.get(item) or pp_idx.get(clean)
            if pp:
                unit_price = float(pp["cost_price"])
                amount = round(qty * unit_price, 2)
                source = "自費"
                note = f"自費商品({pp.get('vendor')})"
            else:
                source = "未匹配"
                note = "自費商品表查無"

        out.append(PricedItem(
            transfer_month=row["transfer_month"],
            direction=direction,
            from_clinic_id=from_id,
            to_clinic_id=to_id,
            item=item,
            item_clean=clean,
            qty=qty,
            vendor=vendor,
            unit_price=unit_price,
            ratio=ratio,
            amount=amount,
            source=source,
            note=note,
        ))

    return out


def summarize(items: list[PricedItem]) -> dict:
    """彙總每月每方向的金額小計與未匹配清單。"""
    by_month_dir: dict[tuple[str, str], dict] = {}
    unmatched: list[PricedItem] = []

    for it in items:
        key = (it.transfer_month, it.direction)
        bucket = by_month_dir.setdefault(key, {"total": 0.0, "items": []})
        if it.amount is not None:
            bucket["total"] += it.amount
        else:
            unmatched.append(it)
        bucket["items"].append(it)

    return {
        "by_month_dir": by_month_dir,
        "unmatched": unmatched,
    }
