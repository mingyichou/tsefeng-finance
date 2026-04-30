"""
中信進出戶 CSV 匯入處理（取代加密 PDF）

對應資料字典 §1.2（澤沛）/ §1.3（澤豐&個人）
檔案範例：
  澤沛中信11504.csv
  澤豐&個人中信11504.csv
編碼：Big5
表頭：第 4 列（前 3 列為元資料：標題 / 資料時間 / 空白）

CSV 欄位：
  日期         → transaction_date (YYYY/MM/DD → YYYY-MM-DD)
  摘要         → summary  (跨行轉/轉帳提/轉帳存/現金/現金提/委代扣/手續費)
  支出         → 寫入 amount 為負值（去千分位）
  存入         → 寫入 amount 為正值（去千分位）
  結餘         → balance（去千分位）
  備註         → channel  (行動網/網銀/ＡＴＭ/存款機/兆豐金/人壽險...)
  轉出入帳號   → counterparty (去除 Excel 文字格式前綴 ')
  註記         → note  ⭐ 院長手動分類欄（最關鍵）

注意：
- 中信 CSV 不含 transaction_time、posting_date
- raw_row_hash 用 (date, amount, balance, summary, note) 組合即可保持唯一
"""

import hashlib
from typing import IO

import pandas as pd


EXPECTED_COLUMNS = [
    "日期", "摘要", "支出", "存入", "結餘", "備註", "轉出入帳號", "註記",
]


def _to_int(val) -> int | None:
    if val is None or pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).replace(",", "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _normalize_date(s) -> str | None:
    if s is None or pd.isna(s):
        return None
    return str(s).strip().replace("/", "-")


def _normalize_str(s) -> str | None:
    if s is None or pd.isna(s):
        return None
    val = str(s).strip()
    return val if val else None


def _normalize_account(s) -> str | None:
    """去除 Excel 文字格式前綴 '（如 '0000137540125004 → 0000137540125004）"""
    val = _normalize_str(s)
    if val and val.startswith("'"):
        val = val[1:].strip()
    return val if val else None


def _row_hash(rec: dict) -> str:
    """
    防重複匯入雜湊
    用 date + amount + balance + summary + note 組合（中信無 time，但同日多筆會用 amount/balance 區分）
    """
    key = "|".join([
        str(rec.get("transaction_date") or ""),
        str(rec.get("amount") or ""),
        str(rec.get("balance") or ""),
        str(rec.get("summary") or ""),
        str(rec.get("note") or ""),
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def parse_ctbc_csv(file_obj: IO, account_id: int) -> list[dict]:
    """
    解析中信進出戶 CSV，回傳可寫入 bank_transactions 的 records list

    Args:
        file_obj: streamlit file_uploader 給的 file-like object
        account_id: bank_accounts.id（澤豐進出戶 / 澤沛進出戶 / 澤豐個人混戶）

    Returns:
        list[dict]：每筆對應 bank_transactions 一個 row
    """
    df = None
    last_err = None
    for encoding in ["big5", "cp950", "utf-8-sig", "utf-8"]:
        try:
            file_obj.seek(0)
            # 前 3 列為元資料（活存明細查詢 / 資料時間 / 空白），表頭在第 4 列
            df = pd.read_csv(file_obj, encoding=encoding, dtype=str, skiprows=3)
            break
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    if df is None:
        raise ValueError(f"CSV 編碼識別失敗（已試 Big5/UTF-8）：{last_err}")

    # 欄位驗證
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV 缺少欄位：{missing}；實際欄位：{list(df.columns)}"
        )

    records = []
    for _, row in df.iterrows():
        ti = _to_int(row.get("支出")) or 0
        de = _to_int(row.get("存入")) or 0
        if ti > 0:
            amount = -ti
        elif de > 0:
            amount = de
        else:
            continue  # 全 0 視為雜訊

        rec = {
            "account_id": account_id,
            "posting_date": None,             # 中信無
            "transaction_date": _normalize_date(row.get("日期")),
            "transaction_time": None,         # 中信 CSV 無時分秒
            "summary": _normalize_str(row.get("摘要")),
            "amount": amount,
            "balance": _to_int(row.get("結餘")),
            "channel": _normalize_str(row.get("備註")),
            "counterparty": _normalize_account(row.get("轉出入帳號")),
            "note": _normalize_str(row.get("註記")),
            "memo_month": None,               # 中信無對應（玉山專屬）
        }
        rec["raw_row_hash"] = _row_hash(rec)

        if not rec["transaction_date"]:
            continue
        records.append(rec)

    return records
