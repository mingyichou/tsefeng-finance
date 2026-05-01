"""
診所支出類匯入處理（Sprint 2.7）

對應 schema:
  - cash_expense       (2.7a) 一筆一列
  - contract_expense   (2.7a) 橫向月度表 → 轉長表
  - check_expense      (2.7b) 一年一檔，每列一個年/月，多廠商重複欄組
  - inventory_transfer (2.7b) 一年一檔，按月區塊，雙欄向 (澤沛pay澤豐 / 澤豐pay澤沛)

⚠️ 調貨 amount 留 NULL，等 Sprint 2.8 product_pricing 表上線後由 trigger 帶入
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


# ─── 支票支出 (check_expense) ───────────────────────────


# 銀行短碼 → 全名（院長指示忽略「延」字）
_BANK_MAP = {
    "玉": "玉山", "玉延": "玉山",
    "中": "中信", "中延": "中信",
}


def parse_check_expense(file_obj: IO, source_filename: str) -> list[dict]:
    """
    解析 @@支票支出115.xlsx 共用檔。

    結構：
      R1: 表頭（廠商/金額/銀行 重複出現）
      R2+: 列 = 民國年/月（如 115/01），每列多組「廠商/金額/銀行」三聯欄

    策略：
      1. R1 找出所有「廠商」欄索引（值 == '廠商'）
      2. 每個廠商欄 c → 金額 c+1 → 銀行 c+2（但實際 layout 有變動，需動態識別）
      3. 對每個資料列，掃所有 (廠商, 金額, 銀行) 三聯，若三者皆有效則收一筆

    院長指示：忽略「玉延/中延」的「延」字，按金額對帳即可。
    """
    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name="支票支出表115", header=None)

    if df.shape[0] < 3:
        return []

    # R1 找「廠商」欄；金額在 +1，銀行在 +2
    header = df.iloc[1]
    vendor_cols: list[int] = []
    for c in range(df.shape[1] - 2):
        if pd.notna(header[c]) and str(header[c]).strip() == "廠商":
            vendor_cols.append(c)

    month_re = re.compile(r"^(\d{3})/(\d{1,2})$")
    records: list[dict] = []
    seen: set[tuple[str, str, str, int]] = set()

    for r in range(2, df.shape[0]):
        cell = df.iloc[r, 0]
        if pd.isna(cell):
            continue
        m = month_re.match(str(cell).strip())
        if not m:
            continue
        roc_y, mo = int(m.group(1)), int(m.group(2))
        if not (100 <= roc_y <= 130 and 1 <= mo <= 12):
            continue
        ad_y = roc_y + 1911
        issue_month = f"{ad_y:04d}-{mo:02d}-01"

        for vc in vendor_cols:
            vendor = _norm_str(df.iloc[r, vc])
            amount = _to_int(df.iloc[r, vc + 1])
            bank_raw = _norm_str(df.iloc[r, vc + 2]) if vc + 2 < df.shape[1] else None
            if not vendor or amount <= 0 or not bank_raw:
                continue
            bank = _BANK_MAP.get(bank_raw, bank_raw)
            if bank not in ("玉山", "中信"):
                continue
            key = (issue_month, vendor, bank, amount)
            if key in seen:
                continue
            seen.add(key)
            records.append({
                "issue_month": issue_month,
                "vendor": vendor,
                "amount": amount,
                "bank": bank,
                "note": (
                    f"原始銀行欄: {bank_raw}（含『延』字，已忽略）"
                    if "延" in bank_raw else None
                ),
            })

    return records


# ─── 調貨整理 (inventory_transfer) ───────────────────────


# 月份區塊標題：'11501調貨整理'
_TRANSFER_MONTH_RE = re.compile(r"^(\d{3})(\d{2})\s*調貨整理")


def parse_inventory_transfer(
    file_obj: IO,
    source_filename: str,
    clinic_zefeng_id: int,
    clinic_zepei_id: int,
) -> list[dict]:
    """
    解析調貨整理 xlsx。

    結構（一年一檔，多月區塊堆疊）：
      R: '{ROCYM}調貨整理'  (區塊標題)
      R+1: 'X pay Y' | 空 | ... | 'Y pay X'
      R+2 ...: 商品名(C0) / 數量(C1)  ||  商品名(C6) / 數量(C7)
      （區塊間有空白列）

    左欄組（C0/C1）= 「澤沛 pay 澤豐」→ 從 澤豐 → 澤沛 調撥（澤豐出貨給澤沛，澤沛欠錢）
    右欄組（C6/C7）= 「澤豐 pay 澤沛」→ 從 澤沛 → 澤豐 調撥

    Args:
        clinic_zefeng_id: 澤豐 clinic_id
        clinic_zepei_id: 澤沛 clinic_id

    Returns:
        list[dict] 對應 inventory_transfer 表（amount/unit_price 暫 None）
    """
    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    records: list[dict] = []
    current_month: str | None = None
    in_block = False  # True 表示目前在某月區塊內的資料列範圍

    for r in range(df.shape[0]):
        c0 = df.iloc[r, 0] if df.shape[1] > 0 else None
        c0_str = str(c0).strip() if pd.notna(c0) else ""

        # 月份區塊標題
        m = _TRANSFER_MONTH_RE.match(c0_str)
        if m:
            roc_y, mo = int(m.group(1)), int(m.group(2))
            ad_y = roc_y + 1911
            current_month = f"{ad_y:04d}-{mo:02d}-01"
            in_block = False  # 等 'pay' 表頭列出現再開始
            continue

        # 表頭列（'澤沛 pay 澤豐'）— 啟動資料收集
        if "pay" in c0_str:
            in_block = True
            continue

        if not (in_block and current_month):
            continue

        # 資料列：左欄 (C0=item, C1=qty) 澤豐→澤沛；右欄 (C6=item, C7=qty) 澤沛→澤豐
        # 左欄組
        if df.shape[1] > 1:
            item_l = _norm_str(df.iloc[r, 0])
            qty_l = _to_float(df.iloc[r, 1])
            if item_l and qty_l and qty_l > 0:
                records.append({
                    "transfer_month": current_month,
                    "from_clinic_id": clinic_zefeng_id,
                    "to_clinic_id": clinic_zepei_id,
                    "item": item_l,
                    "qty": round(qty_l, 2),
                    "unit_price": None,
                    "amount": None,
                })

        # 右欄組
        if df.shape[1] > 7:
            item_r = _norm_str(df.iloc[r, 6])
            qty_r = _to_float(df.iloc[r, 7])
            if item_r and qty_r and qty_r > 0:
                records.append({
                    "transfer_month": current_month,
                    "from_clinic_id": clinic_zepei_id,
                    "to_clinic_id": clinic_zefeng_id,
                    "item": item_r,
                    "qty": round(qty_r, 2),
                    "unit_price": None,
                    "amount": None,
                })

    return records
