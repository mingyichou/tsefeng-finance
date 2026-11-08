"""
調貨整理金額試算引擎（澤豐澤沛金流結算用）

僅試算，不寫回 inventory_transfer.amount。
讀 inventory_transfer + product_pricing + tcm_concentrate_pricing，組出金流明細。

辨識邏輯：
  *  所有品名（索引側與查詢側）先經 _norm：NFKC + casefold
     + 字形變體統一（瓜蔞／栝婁／栝簍… → 栝樓，見 CHAR_VARIANT_GROUPS）
  0. 品項名含 (水) 或 (水藥包) 標註
     → 剝除標註後，到 product_pricing 中 vendor='水藥包' 區塊比對
       （第二個 sheet「自費藥粉&自費商品」的水藥包 vendor 區塊）
  1. 品項名含 (天一|港香蘭|莊松榮|科達|順天堂|仙豐) 之一（括號或 dash 接續）
     → 走 tcm_concentrate_pricing：amount = qty × price × ratio
       ratio = 0.65 (天一/港香蘭) 或 0.70 (莊松榮/科達/順天堂/仙豐)
  2. 不含廠商 keyword
     → 走 product_pricing.cost_price (vendor 寬鬆比對)：amount = qty × cost_price
  3. 精確比對（含別名）失敗
     → 剝註記比對：兩側剝掉括號註記後整個品名相等，同表（科中則同廠商）內
       恰 1 個候選才帶入（砂仁 → 砂仁(不松科)、皂刺 → 皂角刺(不松)）
     → 再前綴模糊比對（前 5 字 → 前 3 字）；唯一候選才帶入，
       2 個以上候選維持未匹配並在 note 列出候選
  4. 兩處都查不到
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
    ["梔子豉湯", "梔子鼓湯"],   # 調貨「鼓」↔ 價目表「豉」
    ["聖癒湯", "聖愈湯"],       # 調貨「愈」↔ 價目表「癒」
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
    # 自費 EPA 魚油（一般版與 mini 版是不同商品，分兩群組）
    ["EPA魚油", "EPA90魚油"],   # 院長 2026-09-03：調貨「EPA魚油」↔ 價目表「EPA90魚油」
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
    ["三合一", "三合一(顆)", "三合一膠囊(顆)", "三合一膠囊"],
    ["佳綠姿", "佳綠姿(顆)", "佳綠姿膠囊(顆)", "佳綠姿膠囊"],
    ["植麗素", "植麗素(顆)", "植麗素膠囊(顆)", "植麗素膠囊"],
    ["塑姿", "塑姿(顆)", "塑姿膠囊(顆)", "塑姿膠囊"],
    [
        "甲殼素", "殼寡糖", "甲殼素(顆)", "殼寡糖(顆)",
        "甲殼素膠囊", "殼寡糖膠囊",
        "甲殼素膠囊(顆)", "殼寡糖膠囊(顆)",
        "甲殼素膠囊(殼寡糖)",
    ],
    [
        "CLA", "CLA(顆)", "紅花籽油", "紅花籽油膠囊(顆)",
        "CLA膠囊", "CLA膠囊(顆)",
        "紅花籽油膠囊",
    ],
    ["非洲芒果", "非洲芒果(顆)", "非洲芒果膠囊(顆)", "非洲芒果膠囊"],
    ["束膳纖", "束膳纖(顆)", "束膳纖膠囊(顆)", "束膳纖膠囊"],
    ["溯本纖", "溯本纖(顆)", "溯本纖膠囊", "溯本纖膠囊(顆)"],
    ["龜鹿二仙膠仙膠", "龜鹿二仙膠仙膠(盒)", "御珍品", "御珍品(龜鹿二仙膠)"],
    # 其它自費商品
    ["洛神花萼膠囊", "洛神花萼蔓越莓膠囊"],
    ["龍循順", "龍循順(粒)", "龍循順(蚓激酶)"],
    # 神美針（院長 2026-09-03；深藍/紫/藍紫 同款、深綠 另一款，兩群獨立）
    ["神美針(深藍)", "神美針(紫)", "神美針(藍紫)", "神美針1015(藍紫)"],
    ["神美針(深綠)", "神美針1215(深綠)"],
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
    [
        "舒敏", "舒敏乳霜", "舒敏保濕乳霜",
        "舒敏保濕乳霜(150ml)", "舒敏舒緩乳霜(150ml)",
    ],
    ["銀膚調理霜", "銀膚特潤調理霜", "銀膚特潤調理霜(100ml)"],
    ["頭皮乳", "頭皮淨化清爽調理乳", "頭皮淨化清爽調理乳(100ml)"],
    ["皮脂平衡潔髮乳", "皮脂平衡潔髮乳(400ml)"],
    # 水藥（調貨檔品名 vs 自費檔品名 用字不一致）
    ["四物湯", "四物湯(水)", "四物湯(水藥包)"],
    ["山楂纖美茶", "山楂鮮美茶"],   # 調貨「纖」↔ 自費檔「鮮」
    # 調貨「飲」↔ 自費檔「茶」；11510 自費檔改名「美聲甘潤茶」（水藥包區唯一美聲品項）
    ["美聲潤喉飲", "美聲潤喉茶", "美聲甘潤茶"],
]


# ─── 字形變體統一（院長 2026-11-09）──────────────────────
# 同一味藥的「習慣用字」不同（瓜蔞／栝樓／栝婁／栝簍…），而且會出現在各種
# 複合品名裡（栝樓仁、栝樓實、栝樓薤白半夏湯、全栝樓…），逐一列別名群組列不完。
# 改在 _norm 階段把所有變體寫法統一成價目表採用的寫法（每群組第一個）；
# 索引建立與查詢兩側都經過 _norm，所以精確／別名／剝註記／模糊比對全部自動受惠。
# ⚠️ 只放「純粹用字不同、絕對是同一味藥」的變體；品種／炮製等會改變藥材意義的字
#    （川木通 vs 木通、生甘草 vs 炙甘草）不要放這裡，改用 ALIAS_GROUPS 逐品項處理。
CHAR_VARIANT_GROUPS: list[list[str]] = [
    # 價目表寫「栝樓」；調貨檔曾出現「栝簍仁」(竹字頭)，院長另提 瓜蔞／栝婁
    ["栝樓", "瓜蔞", "栝婁", "栝簍", "栝蔞", "瓜樓", "瓜婁", "瓜簍", "括樓", "括蔞"],
]


def _build_char_variant_map(groups: list[list[str]]) -> dict[str, str]:
    """{變體(已 NFKC+casefold): 標準寫法}；長變體排前面，避免被短變體先吃掉一部分。"""
    out: dict[str, str] = {}
    for group in groups:
        canon = unicodedata.normalize("NFKC", group[0]).casefold()
        for v in group[1:]:
            out[unicodedata.normalize("NFKC", v).casefold()] = canon
    return dict(sorted(out.items(), key=lambda kv: -len(kv[0])))


_CHAR_VARIANT_MAP = _build_char_variant_map(CHAR_VARIANT_GROUPS)


def _apply_char_variants(s: str) -> str:
    for variant, canon in _CHAR_VARIANT_MAP.items():
        if variant in s:
            s = s.replace(variant, canon)
    return s


def _norm(s: str | None) -> str:
    """NFKC 正規化 + 去前後空白 + casefold + 字形變體統一；用於比對 key。

    casefold 讓英文大小寫不影響比對（EPA/epa、mini/Mini/MINI），
    字形變體統一讓 瓜蔞仁／栝婁仁／栝簍仁 都收斂成價目表的「栝樓仁」。
    索引建立與查詢兩側都經過本函式，兩側一致。
    """
    if not s:
        return ""
    return _apply_char_variants(
        unicodedata.normalize("NFKC", str(s)).strip().casefold()
    )


# ─── 水藥包辨識（院長 2026-06-03）────────────────────────
# 凡品項含「(水)」或「(水藥包)」標註者，一律歸到自費商品檔
# 第二個 sheet「自費藥粉&自費商品」的「水藥包」vendor 區塊比對。
# 比對時把水標註剝除，「四物湯(水)」/「四物湯(水藥包)」/「四物湯」收斂成同一鍵。
_WATER_RE = re.compile(r"[(（]\s*水(?:藥包)?\s*[)）]")


def _has_water_marker(name: str | None) -> bool:
    return bool(_WATER_RE.search(_norm(name)))


def _strip_water_marker(name: str | None) -> str:
    """剝除水標註後的純品名（已 NFKC normalize）。"""
    return _WATER_RE.sub("", _norm(name)).strip()


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


# ─── 前綴模糊比對（院長 2026-07-04）──────────────────────
# 精確比對（含別名）失敗後，改抓「前 5 字相同」→ 再退「前 3 字相同」的候選：
#   恰好 1 個候選 → 直接帶入其價格（如 佳衛暢 → 佳衛暢益生菌）
#   2 個以上候選 → 維持未匹配，note 列出候選供人工確認
# 品名 normalize 後未滿 3 字不做模糊比對（太短易誤配）。
_FUZZY_MIN_CHARS = 3


def _fuzzy_prefix_match(
    query: str, keys,
) -> tuple[str | None, int | None, list[str]]:
    """
    回傳 (唯一命中的 key, 命中前綴長度 5|3, 候選 keys)。
    無命中且無候選時回傳 (None, None, [])。
    """
    q = _norm(query)
    if len(q) < _FUZZY_MIN_CHARS:
        return None, None, []
    for n in (5, 3):
        if len(q) < n:
            continue
        prefix = q[:n]
        cands = [k for k in keys if len(k) >= n and k.startswith(prefix)]
        if len(cands) == 1:
            return cands[0], n, cands
        if len(cands) >= 2:
            return None, n, cands
    return None, None, []


# ─── 剝註記比對（院長 2026-11-09）────────────────────────
# 價目表品名常帶藥局自用的括號註記：砂仁(不松科)、枳實(唯一)、紅花(唯一)、
# 皂角刺(不松)、木通(川)、甘草（生）…；調貨檔只寫純品名「砂仁」。
# 兩字品名不做前綴模糊比對（太短易誤配），所以「砂仁(天一)」一直對不到「砂仁(不松科)」。
# 本層：兩側把括號註記全部剝掉後「整個品名相等」，且同表（科中＝同廠商）內恰 1 個
# 候選才帶入；2 個以上（如 甘草(生)／甘草(炙)）不自動帶入、候選交給人工。
# 排在精確／別名之後、前綴模糊之前：比前綴模糊嚴（要整名相等），又不受字數門檻限制。
_ANNOT_RE = re.compile(r"\([^()]*\)")


def _strip_annotations(key: str) -> str:
    """剝除所有括號註記並去空白（key 須已 _norm，全形括號已轉半形）。"""
    return re.sub(r"\s+", "", _ANNOT_RE.sub("", key)).strip()


def _build_base_index(keys) -> dict[str, list[str]]:
    """{剝註記後的名稱: [原 normalized key, ...]}。"""
    out: dict[str, list[str]] = {}
    for k in keys:
        base = _strip_annotations(k)
        if base:
            out.setdefault(base, []).append(k)
    return out


def _base_lookup(
    names, base_idx: dict[str, list[str]],
) -> tuple[str | None, list[str]]:
    """
    對 names（品名 + 等價別名）逐一做剝註記比對。
    回傳 (唯一命中的原 key 或 None, 候選 keys)；
    任一名稱唯一命中即回傳；全未命中時回傳第一個有候選者的候選清單供提示。
    """
    first_cands: list[str] = []
    for name in names:
        base = _strip_annotations(_norm(name))
        if not base:
            continue
        cands = base_idx.get(base, [])
        if len(cands) == 1:
            return cands[0], cands
        if cands and not first_cands:
            first_cands = cands
    return None, first_cands


def _fmt_candidates(cands: list[str], limit: int = 5) -> str:
    shown = "、".join(cands[:limit])
    if len(cands) > limit:
        shown += f"…等 {len(cands)} 項"
    return shown


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
    clean = (item_name[:m.start()] + item_name[m.end():]).strip(" -_")
    # 只剝掉因去除廠商註而落單的括號；成對的品名註記「砂仁(去殼)」要保留
    # （舊版 strip("()") 會把「砂仁(去殼)(天一)」剝成「砂仁(去殼」）。
    while clean and clean[0] in ")）":
        clean = clean[1:].lstrip(" -_")
    while clean and clean[-1] in "(（":
        clean = clean[:-1].rstrip(" -_")
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


def _build_water_index(rows: list[dict]) -> dict[str, dict]:
    """
    product_pricing 中 vendor='水藥包' 的列
      → {剝除水標註後的 _norm(product_name): row}
    （只收 cost_price 非 NULL；同鍵取先出現者）
    """
    out: dict[str, dict] = {}
    for r in rows:
        if _norm(r.get("vendor")) != "水藥包":
            continue
        name = r.get("product_name")
        if not name or r.get("cost_price") is None:
            continue
        key = _strip_water_marker(name)
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
    water_idx = _build_water_index(product_pricing_rows)

    # 模糊比對用：各廠商的科中品名清單（normalized）
    tcm_names_by_vendor: dict[str, list[str]] = {}
    for (v_key, n_key) in tcm_idx:
        tcm_names_by_vendor.setdefault(v_key, []).append(n_key)

    # 剝註記比對用索引：{剝掉括號註記後的名稱: [原 key, ...]}
    water_base_idx = _build_base_index(water_idx.keys())
    pp_base_idx = _build_base_index(pp_idx.keys())
    tcm_base_by_vendor: dict[str, dict[str, list[str]]] = {
        v_key: _build_base_index(names)
        for v_key, names in tcm_names_by_vendor.items()
    }

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

        if _has_water_marker(item):
            # 水藥包：剝除水標註後到「水藥包」vendor 區塊比對
            # （同時嘗試剝水後的等價別名）
            vendor, clean = None, _strip_water_marker(item)
            pp = None
            for cand in _equivalents_of(clean):
                pp = water_idx.get(_strip_water_marker(cand))
                if pp:
                    break
            if pp:
                unit_price = float(pp["cost_price"])
                amount = round(qty * unit_price, 2)
                source = "自費(水藥包)"
                note = f"水藥包：{clean}"
            else:
                # 剝註記比對：兩側剝掉括號註記後整名相等且唯一
                bhit, bcands = _base_lookup(
                    [_strip_water_marker(c) for c in _equivalents_of(clean)],
                    water_base_idx,
                )
                hit, lv, cands = None, None, []
                if not bhit:
                    # 模糊比對：前 5 字 → 前 3 字，唯一命中才帶入
                    hit, lv, cands = _fuzzy_prefix_match(clean, water_idx.keys())
                if bhit:
                    pp = water_idx[bhit]
                    unit_price = float(pp["cost_price"])
                    amount = round(qty * unit_price, 2)
                    source = "自費(水藥包)"
                    note = f"剝註記命中：{clean} → {pp['product_name']}"
                elif hit:
                    pp = water_idx[hit]
                    unit_price = float(pp["cost_price"])
                    amount = round(qty * unit_price, 2)
                    source = "自費(水藥包)"
                    note = f"模糊命中(前{lv}字)：{clean} → {pp['product_name']}"
                elif cands:
                    source = "未匹配"
                    note = (
                        f"水藥包表前{lv}字有 {len(cands)} 個候選："
                        f"{_fmt_candidates(cands)}｜請人工確認品名"
                    )
                elif bcands:
                    source = "未匹配"
                    note = (
                        f"水藥包表剝註記後有 {len(bcands)} 個候選："
                        f"{_fmt_candidates(bcands)}｜請人工確認品名"
                    )
                else:
                    source = "未匹配"
                    note = f"水藥包表查無 {clean}（含別名/剝註記/模糊）"
        elif vendor:
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
                # 剝註記比對：同廠商內兩側剝掉括號註記後整名相等且唯一
                # （砂仁 → 砂仁(不松科)、皂刺 → 別名 皂角刺 → 皂角刺(不松)）
                bhit, bcands = _base_lookup(
                    _equivalents_of(clean), tcm_base_by_vendor.get(v_key, {})
                )
                hit, lv, cands = None, None, []
                if not bhit:
                    # 模糊比對：只在同廠商的品名內找，前 5 字 → 前 3 字
                    hit, lv, cands = _fuzzy_prefix_match(
                        clean, tcm_names_by_vendor.get(v_key, [])
                    )
                if bhit:
                    tcm = tcm_idx[(v_key, bhit)]
                    unit_price = float(tcm["price"])
                    ratio = BRAND_RATIO[vendor]
                    amount = round(qty * unit_price * ratio, 2)
                    source = f"科中{tcm['category']}"
                    note = f"剝註記命中：{clean} → {tcm['product_name']}"
                elif hit:
                    tcm = tcm_idx[(v_key, hit)]
                    unit_price = float(tcm["price"])
                    ratio = BRAND_RATIO[vendor]
                    amount = round(qty * unit_price * ratio, 2)
                    source = f"科中{tcm['category']}"
                    note = f"模糊命中(前{lv}字)：{clean} → {tcm['product_name']}"
                elif cands:
                    source = "未匹配"
                    note = (
                        f"科中表({vendor})前{lv}字有 {len(cands)} 個候選："
                        f"{_fmt_candidates(cands)}｜請人工確認品名"
                    )
                elif bcands:
                    source = "未匹配"
                    note = (
                        f"科中表({vendor})剝註記後有 {len(bcands)} 個候選："
                        f"{_fmt_candidates(bcands)}｜請人工確認品名"
                    )
                else:
                    source = "未匹配"
                    note = f"科中表查無 {vendor} × {clean}（含別名/剝註記/模糊）"
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
                # 剝註記比對：兩側剝掉括號註記後整名相等且唯一
                # （如 瑪卡(上賀) → 瑪卡(200G)）
                bhit, bcands = _base_lookup(candidates, pp_base_idx)
                hit, lv, cands = None, None, []
                if not bhit:
                    # 模糊比對：前 5 字 → 前 3 字，唯一命中才帶入
                    # （如 佳衛暢 → 佳衛暢益生菌、攝護通膠囊 → 攝護通）
                    hit, lv, cands = _fuzzy_prefix_match(clean, pp_idx.keys())
                if bhit:
                    pp = pp_idx[bhit]
                    unit_price = float(pp["cost_price"])
                    amount = round(qty * unit_price, 2)
                    source = "自費"
                    note = (
                        f"自費商品({pp.get('vendor')})｜"
                        f"剝註記命中：{clean} → {pp['product_name']}"
                    )
                elif hit:
                    pp = pp_idx[hit]
                    unit_price = float(pp["cost_price"])
                    amount = round(qty * unit_price, 2)
                    source = "自費"
                    note = (
                        f"自費商品({pp.get('vendor')})｜"
                        f"模糊命中(前{lv}字)：{clean} → {pp['product_name']}"
                    )
                elif cands:
                    source = "未匹配"
                    note = (
                        f"自費商品表前{lv}字有 {len(cands)} 個候選："
                        f"{_fmt_candidates(cands)}｜請人工確認品名"
                    )
                elif bcands:
                    source = "未匹配"
                    note = (
                        f"自費商品表剝註記後有 {len(bcands)} 個候選："
                        f"{_fmt_candidates(bcands)}｜請人工確認品名"
                    )
                else:
                    source = "未匹配"
                    note = "自費商品表查無（含別名/剝註記/模糊）"

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
