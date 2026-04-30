"""
醫療費用付款通知書 HTML 批次匯入處理（Sprint 2.3）

對應資料字典 §2.1
檔案範例：3807350271_14_11503_7021_idc_1150427_062559_00859.html
編碼：Big5 / CP950

檔名規則：
  {inst_code}_14_{rocym}_7021_idc_{rocymd}_{hhmmss}_{seq}.html
    inst_code: 3807350271=澤豐, 3807340051=澤沛
    rocym    : 民國年月（11503 = 民國 115 年 03 月 = 西元 2026-03）
    rocymd   : 民國年月日（下載日期）

兩種版式（皆以「實付金額」為最終金流金額）：
  A. 暫付型：右欄「暫付費用」(受理數 A / 暫付成數 B / 點值 C / 暫付金額)
  B. 核定型：左欄「核定費用」(申請數 / 核減數 / 點值調整數 / 核定金額)
            常見於自墊核退、補付項目，A/B/C 為空

⚠️ paid_amount 取「實付金額」而非「暫付金額(A*B*C)」（資料字典 §2.1 文字描述
   為暫付金額，但核定型版本暫付金額為空，實付金額才是與玉山健保入帳對齊的權威值）
"""

import re
from pathlib import Path
from typing import IO

from bs4 import BeautifulSoup


FILENAME_RE = re.compile(
    r"^(\d{10})_14_(\d{5})_7021_idc_(\d{7})_\d{6}_\d+\.html?$",
    re.IGNORECASE,
)

INST_CODE_TO_SHORT = {
    "3807350271": "澤豐",
    "3807340051": "澤沛",
}


def _decode_big5(raw: bytes) -> str:
    last_err: Exception | None = None
    for enc in ("big5", "cp950", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError as e:
            last_err = e
    raise ValueError(f"HTML 解碼失敗（已試 Big5/CP950/UTF-8）：{last_err}")


def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    cleaned = re.sub(r"[\s$　,]", "", s)
    if not cleaned or cleaned in ("-", "—"):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _to_float(s: str | None) -> float | None:
    if s is None:
        return None
    cleaned = re.sub(r"[\s$　,]", "", s)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _roc_to_iso(roc_str: str | None) -> str | None:
    """民國日期 → ISO。115/04/27 → 2026-04-27；115/03 → 2026-03-01"""
    if not roc_str:
        return None
    m = re.match(r"(\d{2,3})/(\d{1,2})(?:/(\d{1,2}))?", roc_str.strip())
    if not m:
        return None
    yr = int(m.group(1)) + 1911
    mo = int(m.group(2))
    d = int(m.group(3)) if m.group(3) else 1
    return f"{yr:04d}-{mo:02d}-{d:02d}"


def parse_filename(filename: str) -> dict:
    """
    解析檔名取得機構碼、服務年月、下載日期。

    Returns:
        {
          'inst_code': '3807350271',
          'clinic_short': '澤豐',
          'service_month': '2026-03-01',
          'download_date': '2026-04-27',
        }
    """
    name = Path(filename).name
    m = FILENAME_RE.match(name)
    if not m:
        raise ValueError(f"檔名格式不符健保通知書規則：{name}")

    inst_code = m.group(1)
    rocym = m.group(2)
    rocymd = m.group(3)

    roc_y, roc_m = int(rocym[:3]), int(rocym[3:])
    service_month = f"{roc_y + 1911:04d}-{roc_m:02d}-01"

    roc_y2 = int(rocymd[:3])
    roc_m2 = int(rocymd[3:5])
    roc_d2 = int(rocymd[5:7])
    download_date = f"{roc_y2 + 1911:04d}-{roc_m2:02d}-{roc_d2:02d}"

    return {
        "inst_code": inst_code,
        "clinic_short": INST_CODE_TO_SHORT.get(inst_code),
        "service_month": service_month,
        "download_date": download_date,
    }


_HEADER_PATTERNS = {
    "apply_date": re.compile(r"申請日期\s*[︰:]\s*(\d{2,3}/\d{1,2}/\d{1,2})"),
    "payment_date": re.compile(r"付款日期\s*[︰:]\s*(\d{2,3}/\d{1,2}/\d{1,2})"),
    "service_month_text": re.compile(r"費用年月\s*[︰:]\s*(\d{2,3}/\d{1,2})"),
}


def _build_kv(soup: BeautifulSoup) -> dict[str, str]:
    """
    把表格的 label-value pairs 抽成 dict：
      - 4-cell <tr>: [label_L, value_L, label_R, value_R]（核定/暫付雙欄）
      - 2-cell <tr>: [label, value]（實付金額這類單欄列）
    label 末尾全形空白統一去掉；同 label 不覆蓋（先到先得）。
    """
    kv: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 4:
            pairs = [(tds[0], tds[1]), (tds[2], tds[3])]
        elif len(tds) == 2:
            pairs = [(tds[0], tds[1])]
        else:
            continue
        for label_td, value_td in pairs:
            label = label_td.get_text(strip=True).strip("　 ").rstrip()
            value = value_td.get_text(strip=True).strip("　 ").rstrip()
            if label and value and value not in ("　", ""):
                kv.setdefault(label, value)
    return kv


def _kv_first(kv: dict[str, str], *keys: str) -> str | None:
    for k in keys:
        if k in kv:
            return kv[k]
    return None


def _extract_deduction(soup: BeautifulSoup, body_text: str) -> tuple[int, str | None]:
    """
    扣款表是 6-cell 結構：[扣款原因, 費用年月, 醫事機構類別, 扣款金額, 扣款業務組, 扣款醫事機構代碼]
    無扣款時整列為「無扣款資料！」。
    """
    if "無扣款資料" in body_text:
        return 0, None
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 6:
            continue
        reason = tds[0].get_text(strip=True)
        if reason in ("扣款原因", ""):
            continue
        amt = _to_int(tds[3].get_text(strip=True))
        if amt:
            return amt, reason or None
    return 0, None


def parse_nhi_notice_html(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
) -> dict:
    """
    解析一份醫療費用付款通知書 HTML，回傳 nhi_payment_notices 的單筆 dict。

    Args:
        file_obj: streamlit file_uploader 提供的 file-like 物件（讀 bytes）
        source_filename: 原始檔名（用於檔名解析 + 防重複匯入鍵）
        clinic_id: clinics.id（呼叫端依檔名 inst_code 解析）

    Raises:
        ValueError: 檔名格式錯、編碼錯、找不到關鍵欄位
    """
    file_obj.seek(0)
    raw = file_obj.read()
    text = raw if isinstance(raw, str) else _decode_big5(raw)

    soup = BeautifulSoup(text, "html.parser")
    body_text = soup.get_text(separator="\n", strip=True)

    apply_date = _roc_to_iso(
        m.group(1) if (m := _HEADER_PATTERNS["apply_date"].search(body_text)) else None
    )
    payment_date = _roc_to_iso(
        m.group(1) if (m := _HEADER_PATTERNS["payment_date"].search(body_text)) else None
    )

    fn_meta = parse_filename(source_filename)

    kv = _build_kv(soup)

    payment_type = kv.get("付款別")

    # 受理數 A（暫付型）/ 申請數（核定型）
    applied_amount = _to_int(_kv_first(kv, "受理數 A", "受理數", "申請數"))

    interim_ratio = _to_float(_kv_first(kv, "暫付成數 B", "暫付成數"))
    point_value = _to_float(_kv_first(kv, "點值   C", "點值 C", "點值"))

    # 實付金額：兩種版式皆有；對齊玉山健保入帳的權威金額
    paid_amount = _to_int(kv.get("實付金額"))

    deduction_amount, deduction_reason = _extract_deduction(soup, body_text)

    if applied_amount is None:
        raise ValueError(f"{source_filename}: 找不到 受理數/申請數")
    if paid_amount is None:
        raise ValueError(f"{source_filename}: 找不到 實付金額")
    if not apply_date:
        raise ValueError(f"{source_filename}: 找不到 申請日期")
    if not payment_date:
        raise ValueError(f"{source_filename}: 找不到 付款日期")

    return {
        "clinic_id": clinic_id,
        "service_month": fn_meta["service_month"],
        "apply_date": apply_date,
        "payment_date": payment_date,
        "payment_type": payment_type,
        "applied_amount": applied_amount,
        "interim_ratio_pct": interim_ratio,
        "point_value": point_value,
        "paid_amount": paid_amount,
        "deduction_amount": deduction_amount,
        "deduction_reason": deduction_reason,
        "source_filename": Path(source_filename).name,
    }
