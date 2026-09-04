"""
銀行交易重複防護（玉山健保戶 / 中信進出戶共用）

背景（2026-09-05 事故）：
  院長把玉山 11508 CSV 用 Excel 補上匯款帳號後重傳。Excel 存檔把日期
  2026/08/26 改成 2026/8/26，舊版 _normalize_date 只把 / 換成 -，
  raw_row_hash 整月全部不同 → 11 筆全部重複匯入（UI 看不出來，因 DATE
  欄位入庫後會正規化成同一天）。

三層防護：
  1. 解析層  canonical_date / canonical_time：日期時間零補位標準化，
             Excel 存過的檔與銀行原始檔算出同一個 raw_row_hash。
  2. 匯入層  plan_import：以「邏輯鍵」(帳戶, 日期, 金額, 餘額, 摘要) 比對
             資料庫既有列 —— 已存在 → 只補值（counterparty / note /
             memo_month / channel / posting_date；來檔非空才覆蓋，
             「補值不清值」）；不存在 → 新增。同鍵多筆用多重集合逐一配對，
             合法的同日同額同餘額交易（極罕見）不會被誤判。
  3. 診斷層  find_duplicate_groups / choose_merge：掃出已存在的重複組，
             保留最早一筆（其 hash 是銀行原始格式），其餘欄位「最新非空值優先」
             合併到保留列後刪除多餘列。data_health 的完整度診斷也用它警示。

不用 DB UNIQUE 索引硬擋的原因：同日同額同餘額同摘要在中信（無時間欄）
理論上可合法出現（如同日轉出→退回→再轉出），硬擋會把合法列拒於門外且
錯誤訊息難懂；多重集合配對能正確處理。
"""

from __future__ import annotations

import re
from collections import defaultdict

_DATE_RE = re.compile(r"(\d{3,4})\s*[/\-.年]\s*(\d{1,2})\s*[/\-.月]\s*(\d{1,2})")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?")

# 匯入 / 合併時可補值的欄位（不含邏輯鍵欄位與 raw_row_hash）
INFO_FIELDS = ("counterparty", "note", "memo_month", "channel", "posting_date")

SELECT_COLS = (
    "id, account_id, posting_date, transaction_date, transaction_time, "
    "summary, amount, balance, channel, counterparty, note, memo_month, "
    "raw_row_hash, imported_at"
)


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN
        return True
    return str(v).strip() in ("", "--", "nan", "NaT", "None")


def canonical_date(s) -> str | None:
    """任何常見寫法 → 'YYYY-MM-DD'（零補位）。

    接受 2026/08/26、2026/8/26、2026-8-26、2026.8.26、2026年8月26日、
    Excel 轉成 datetime 的 '2026/8/26 00:00:00'；民國年 115/8/26 也轉西元。
    解析不出 → None。
    """
    if _is_blank(s):
        return None
    m = _DATE_RE.search(str(s))
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    if y < 1000:  # 民國年
        y += 1911
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def canonical_time(s) -> str | None:
    """'9:06:01' / '09:06' / '上午 09:06:01' / '下午 1:05' → 'HH:MM:SS'。"""
    if _is_blank(s):
        return None
    txt = str(s)
    m = _TIME_RE.search(txt)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    se = int(m.group(3) or 0)
    low = txt.lower()
    if ("下午" in txt or "pm" in low) and h < 12:
        h += 12
    if ("上午" in txt or "am" in low) and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= se <= 59):
        return None
    return f"{h:02d}:{mi:02d}:{se:02d}"


def logical_key(rec: dict) -> tuple:
    """同一帳戶內辨識「同一筆交易」的鍵：(帳戶, 日期, 金額, 餘額, 摘要)。

    不含時間（Excel 可能吃掉秒數）、不含 counterparty / note 等可事後補的欄位。
    餘額是最強的區分欄：同日同額但餘額不同就是兩筆不同交易。
    """
    bal = rec.get("balance")
    return (
        int(rec["account_id"]),
        canonical_date(rec.get("transaction_date")),
        int(rec.get("amount") or 0),
        None if _is_blank(bal) else int(bal),
        str(rec.get("summary") or "").strip(),
    )


def _norm_val(field: str, v):
    if _is_blank(v):
        return None
    if field == "posting_date":
        return canonical_date(v)
    return str(v).strip()


def fill_changes(existing: dict, incoming: dict) -> dict:
    """來檔非空且與既有不同的補值欄位 → {field: (old, new)}；來檔空值不清既有值。"""
    changes = {}
    for f in INFO_FIELDS:
        new = _norm_val(f, incoming.get(f))
        if new is None:
            continue
        old = _norm_val(f, existing.get(f))
        if old != new:
            changes[f] = (old, new)
    return changes


def plan_import(existing_rows: list[dict], records: list[dict]) -> dict:
    """
    把待匯入 records 與資料庫既有列（同帳戶、同日期範圍）配對。

    Returns:
      inserts:  list[dict]  資料庫沒有 → 要新增的 record
      updates:  list[dict]  {"id", "row", "changes"} 已存在但來檔帶了新資料 → 補值
      skips:    list[dict]  已存在且無新資料
      unmatched_existing: list[dict] 既有列沒被本檔任何一筆對到（多半是舊的重複列）
    """
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for row in sorted(existing_rows, key=lambda r: r["id"]):
        by_key[logical_key(row)].append(row)
    used: dict[tuple, int] = defaultdict(int)

    inserts, updates, skips = [], [], []
    for rec in records:
        k = logical_key(rec)
        pool = by_key.get(k, [])
        i = used[k]
        if i < len(pool):
            used[k] = i + 1
            row = pool[i]
            changes = fill_changes(row, rec)
            if changes:
                updates.append({"id": row["id"], "row": row, "changes": changes})
            else:
                skips.append(rec)
        else:
            inserts.append(rec)

    unmatched = [row for k, pool in by_key.items() for row in pool[used[k]:]]
    unmatched.sort(key=lambda r: r["id"])
    return {
        "inserts": inserts,
        "updates": updates,
        "skips": skips,
        "unmatched_existing": unmatched,
    }


def find_duplicate_groups(rows: list[dict]) -> list[list[dict]]:
    """同邏輯鍵 ≥2 筆的群組；每組依 id 升冪，群組依日期 / id 排序。"""
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_key[logical_key(r)].append(r)
    groups = [sorted(g, key=lambda r: r["id"]) for g in by_key.values() if len(g) > 1]
    groups.sort(key=lambda g: (str(g[0].get("transaction_date") or ""), g[0]["id"]))
    return groups


def choose_merge(group: list[dict]) -> dict:
    """
    決定一組重複列怎麼清：保留最早 id（通常是銀行原始檔上傳的那筆，
    raw_row_hash 為原始格式），其餘刪除；補值欄位取「最新非空值」
    （較晚上傳的檔通常是修正版）。

    Returns {"keep": row, "delete": [row...], "changes": {field: (old, new)}}
    """
    g = sorted(group, key=lambda r: r["id"])
    keep, others = g[0], g[1:]
    changes = {}
    for f in INFO_FIELDS:
        newest = None
        for o in reversed(others):  # 最新在前
            v = _norm_val(f, o.get(f))
            if v is not None:
                newest = v
                break
        if newest is None:
            continue
        old = _norm_val(f, keep.get(f))
        if old != newest:
            changes[f] = (old, newest)
    return {"keep": keep, "delete": others, "changes": changes}


def fetch_bank_rows_paged(sb, account_ids=None, date_from=None, date_to=None,
                          chunk: int = 1000) -> list[dict]:
    """分頁抓 bank_transactions（Supabase 預設 1000 筆靜默截斷）。date_to 含當日。"""
    out: list[dict] = []
    offset = 0
    while True:
        q = sb.table("bank_transactions").select(SELECT_COLS)
        if account_ids:
            q = q.in_("account_id", list(account_ids))
        if date_from:
            q = q.gte("transaction_date", date_from)
        if date_to:
            q = q.lte("transaction_date", date_to)
        data = q.order("id").range(offset, offset + chunk - 1).execute().data or []
        out.extend(data)
        if len(data) < chunk:
            break
        offset += chunk
    return out
