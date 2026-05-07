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
import unicodedata
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


# ─── 別名等價群組（院長 2026-05-08 提供）───────────────
# 同一群組內所有名稱視為等價；查表時會嘗試每個名稱直到命中。
# 比對前會做 NFKC normalize（處理全形/半形差異）。
ALIAS_GROUPS: list[list[str]] = [
    # 中藥單方
    ["山楂", "山查"],
    ["紫菀", "紫苑"],
    ["太子參", "太子蔘"],
    ["黨參", "黨蔘"],
    ["皂角刺", "皂刺"],
    ["浙貝母", "貝母"],
    ["製附子", "附子"],
    # 中藥複方（湯/散互通 + 藥丸系列括號 vs dash）
    ["辛夷清肺湯", "辛夷清肺散"],
    ["六味地黃丸(藥丸)", "六味地黃丸-藥丸"],
    ["杞菊地黃丸(藥丸)", "杞菊地黃丸-藥丸"],
    ["天王補心丹(藥丸)", "天王補心丹-藥丸"],
    ["四物丸(藥丸)", "四物丸-藥丸"],
    ["龜鹿二仙丸(藥丸)", "龜鹿二仙丸-藥丸"],
    ["左歸丸(藥丸)", "左歸丸-藥丸"],
    ["濟生腎氣丸(藥丸)", "濟生腎氣丸-藥丸"],
    ["健步虎潛丸(藥丸)", "健步虎潛丸-藥丸"],
    ["右歸丸(藥丸)", "右歸丸-藥丸"],
    ["加味逍遙丸(藥丸)", "加味逍遙丸-藥丸"],
    # 自費 EPA 魚油
    [
        "魚油mini", "EPA魚油mini", "EPA魚油MINI",
        "EPA90魚油MINI", "EPA90魚油mini",
    ],
    # 自費膠囊（瓶 vs 顆 vs 簡繁字）
    [
        "納豆紅麴", "納豆紅麴膠囊", "納豆紅麴膠囊(30顆)",
        "纳豆紅麴", "纳豆紅麴膠囊",  # 簡體「纳」
    ],
    ["瑪卡", "馬卡", "瑪卡(200G)", "馬卡(200G)"],
    ["特級瑪卡", "特級馬卡", "特輯瑪卡"],
    ["西洋蔘", "西洋參", "西洋蔘(100G)"],
    ["三合一膠囊(顆)", "三合一膠囊"],
    ["佳綠姿膠囊(顆)", "佳綠姿膠囊"],
    ["植麗素(顆)", "植麗素膠囊(顆)", "植麗素膠囊", "植麗素"],
    ["塑姿膠囊(顆)", "塑姿膠囊"],
    [
        "甲殼素膠囊", "殼寡糖膠囊",
        "甲殼素膠囊(顆)", "殼寡糖膠囊(顆)",
        "甲殼素膠囊(殼寡糖)",
    ],
    [
        "CLA膠囊", "CLA膠囊(顆)",
        "紅花籽油膠囊", "紅花籽油膠囊(顆)",
    ],
    ["非洲芒果膠囊(顆)", "非洲芒果膠囊"],
    ["束膳纖(顆)", "束膳纖膠囊(顆)", "束膳纖膠囊", "束膳纖"],
    ["溯本纖(顆)", "溯本纖膠囊", "溯本纖"],
    ["龜鹿二仙膠仙膠", "龜鹿二仙膠仙膠(盒)", "御珍品", "御珍品(龜鹿二仙膠)"],
    # 其它自費商品
    ["洛神花萼膠囊", "洛神花萼蔓越莓膠囊"],
    ["龍循順", "龍循順(粒)", "龍循順(蚓激酶)"],
    # 護具
    ["護腕", "運動遠紅外線護腕"],
    ["支撐護腕", "超透氣支撐護腕"],
    ["護肘", "運動遠紅外線護肘"],
    ["護膝", "運動遠紅外線護膝"],
    ["護踝", "運動遠紅外線護踝"],
    [
        "護腰", "護腰S", "護腰M", "護腰L", "護腰XL",
        "竹炭透氣護腰",
    ],
    ["護腰XXL", "護腰2XL", "護腰3XL", "竹炭透氣護腰(大SIZE)"],
    ["高背架護腰S/M", "高背架護腰L/XL", "高背架護腰"],
    # 電極片
    ["電極片-小", "電極片(小)", "電極片"],
    ["電極片-大", "電極片(大)"],
    # 外用藥/凝膠
    ["金絲膏", "金絲膏水布", "金絲膏-水布", "金絲膏(水布)"],
    ["全方位", "全方位舒緩凝膠", "全方位舒緩凝膠(30ml)"],
    ["舒敏", "舒敏保濕乳霜", "舒敏保濕乳霜(150ml)", "舒敏舒緩乳霜(150ml)"],
    ["銀膚調理霜", "銀膚特潤調理霜", "銀膚特潤調理霜(100ml)"],
    ["頭皮乳", "頭皮淨化清爽調理乳", "頭皮淨化清爽調理乳(100ml)"],
    ["皮脂平衡潔髮乳", "皮脂平衡潔髮乳(400ml)"],
    # 水藥
    ["四物湯(水)", "四物湯(水藥包)"],
]


def _norm(s: str | None) -> str:
    """NFKC 正規化 + 去前後空白；用於比對 key。"""
    if not s:
        return ""
    return unicodedata.normalize("NFKC", str(s)).strip()


def _build_alias_index(groups: list[list[str]]) -> dict[str, list[str]]:
    """
    建立 normalized_name → list of equivalent original names。
    含 transitive closure（同一名稱出現在多群組會合併）。
    """
    out: dict[str, list[str]] = {}
    for group in groups:
        # 找出本群組與既有索引交集的所有 forms
        merged_set: set[str] = set(group)
        for name in group:
            existing = out.get(_norm(name))
            if existing:
                merged_set.update(existing)
        merged = list(merged_set)
        for name in merged_set:
            out[_norm(name)] = merged
    return out


_ALIAS_INDEX = _build_alias_index(ALIAS_GROUPS)


def _equivalents_of(name: str) -> list[str]:
    """回傳所有等價名（含自己）。沒設 alias 時回傳 [name]。"""
    eq = _ALIAS_INDEX.get(_norm(name))
    if eq:
        # 確保自己也在裡面（如果沒 normalize 重合則加入）
        if name not in eq:
            return [name] + eq
        return eq
    return [name]


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
    tcm_concentrate_pricing → {(_norm(vendor), _norm(product_name)): row}
    同 (vendor, product_name) 同時在複方/單方出現時，以複方優先。
    """
    out: dict[tuple[str, str], dict] = {}
    for cat in ("複方", "單方"):
        for r in rows:
            if r.get("category") != cat:
                continue
            key = (_norm(r["vendor"]), _norm(r["product_name"]))
            if key in out:
                continue
            out[key] = r
    return out


def _build_pricing_index(rows: list[dict]) -> dict[str, dict]:
    """
    product_pricing → {_norm(product_name): row}
    （不分廠商；同 product_name 取 cost_price 非 NULL 者）
    """
    out: dict[str, dict] = {}
    for r in rows:
        name = r.get("product_name")
        if not name:
            continue
        if r.get("cost_price") is None:
            continue
        key = _norm(name)
        if key in out:
            continue
        out[key] = r
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
            # 走科中：嘗試 clean 名 + 所有等價別名
            v_key = _norm(vendor)
            tcm = None
            tried_alias: str | None = None
            for cand in _equivalents_of(clean):
                tcm = tcm_idx.get((v_key, _norm(cand)))
                if tcm:
                    if cand != clean:
                        tried_alias = cand
                    break
            if tcm:
                unit_price = float(tcm["price"])
                ratio = BRAND_RATIO[vendor]
                amount = round(qty * unit_price * ratio, 2)
                source = f"科中{tcm['category']}"
                if tried_alias:
                    note = f"別名命中：{clean} → {tried_alias}"
            else:
                source = "未匹配"
                note = f"科中表查無 {vendor} × {clean}（含別名）"
        else:
            # 走自費商品（不分廠商，僅靠 product_name）
            # 嘗試順序：item 原名 + clean 名 + item 等價 + clean 等價
            pp = None
            tried_alias = None
            seen_keys: set[str] = set()
            candidates: list[str] = []
            for src in (item, clean):
                for c in _equivalents_of(src):
                    k = _norm(c)
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    candidates.append(c)
            for cand in candidates:
                pp = pp_idx.get(_norm(cand))
                if pp:
                    if cand not in (item, clean):
                        tried_alias = cand
                    break
            if pp:
                unit_price = float(pp["cost_price"])
                amount = round(qty * unit_price, 2)
                source = "自費"
                msg = f"自費商品({pp.get('vendor')})"
                if tried_alias:
                    msg += f"｜別名命中：→ {tried_alias}"
                note = msg
            else:
                source = "未匹配"
                note = "自費商品表查無（含別名）"

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
