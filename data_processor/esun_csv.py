"""
玉山健保戶 CSV 匯入處理

對應資料字典 §1.1
檔案範例：澤豐中醫健保戶-玉山.csv、澤沛中醫健保戶-玉山.csv
編碼：UTF-8 (含 BOM)
表頭：第 1 列即欄位名

欄位 mapping：
  序號                  → 忽略（每次下載重編）
  帳務日期              → posting_date (YYYY/MM/DD → YYYY-MM-DD)
  實際交易日期          → transaction_date
  實際交易時間          → transaction_time (HH:MM:SS)
  摘要                  → summary
  提                    → 拆解：>0 寫入 amount 為負值
  存                    → 拆解：>0 寫入 amount 為正值
  餘額                  → balance (去千分位逗號)
  備註                  → memo (含月份代碼如 11502)
  轉出入銀行代號/帳號   → counterparty ('--' → NULL)
"""

import hashlib
from typing import IO

import pandas as pd


EXPECTED_COLUMNS = [
    "序號", "帳務日期", "實際交易日期", "實際交易時間",
    "摘要", "提", "存", "餘額", "備註", "轉出入銀行代號/帳號",
]


def _to_int(val) -> int | None:
    """含千分位逗號的字串 → int；空值 → None"""
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
    """YYYY/MM/DD → YYYY-MM-DD"""
    if s is None or pd.isna(s):
        return None
    return str(s).strip().replace("/", "-")


def _normalize_str(s) -> str | None:
    """空字串/--/NaN → None"""
    if s is None or pd.isna(s):
        return None
    val = str(s).strip()
    if val in ("", "--"):
        return None
    return val


def _row_hash(rec: dict) -> str:
    """根據 transaction_date/time/amount/balance/summary 雜湊（防重複匯入）"""
    key = "|".join([
        str(rec.get("transaction_date") or ""),
        str(rec.get("transaction_time") or ""),
        str(rec.get("amount") or ""),
        str(rec.get("balance") or ""),
        str(rec.get("summary") or ""),
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def parse_esun_csv(file_obj: IO, account_id: int) -> list[dict]:
    """
    解析玉山健保戶 CSV，回傳可寫入 bank_transactions 的 records list

    Args:
        file_obj: streamlit file_uploader 給的 file-like object（BytesIO）
        account_id: bank_accounts.id（呼叫端確保已建好）

    Returns:
        list[dict]: 每筆對應 bank_transactions 一個 row

    Raises:
        ValueError: CSV 編碼無法識別、欄位缺漏
    """
    # 嘗試多種編碼（玉山下載通常是 UTF-8 with BOM）
    df = None
    last_err = None
    for encoding in ["utf-8-sig", "utf-8", "big5", "cp950"]:
        try:
            file_obj.seek(0)
            df = pd.read_csv(file_obj, encoding=encoding, dtype=str)
            break
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    if df is None:
        raise ValueError(f"CSV 編碼識別失敗（已試 UTF-8/Big5）：{last_err}")

    # 欄位驗證
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV 缺少欄位：{missing}；實際欄位：{list(df.columns)}")

    records = []
    for _, row in df.iterrows():
        # 計算 amount：提 為負、存 為正
        ti = _to_int(row.get("提")) or 0
        de = _to_int(row.get("存")) or 0
        if ti > 0:
            amount = -ti
        elif de > 0:
            amount = de
        else:
            continue  # 全 0 視為雜訊行

        rec = {
            "account_id": account_id,
            "posting_date": _normalize_date(row.get("帳務日期")),
            "transaction_date": _normalize_date(row.get("實際交易日期")),
            "transaction_time": _normalize_str(row.get("實際交易時間")),
            "summary": _normalize_str(row.get("摘要")),
            "amount": amount,
            "balance": _to_int(row.get("餘額")),
            "memo_month": _normalize_str(row.get("備註")),
            "counterparty": _normalize_str(row.get("轉出入銀行代號/帳號")),
        }
        rec["raw_row_hash"] = _row_hash(rec)

        # transaction_date 是 NOT NULL，沒有就跳過
        if not rec["transaction_date"]:
            continue

        records.append(rec)

    return records
