"""
員工薪資匯入處理（Sprint 2.8c）

對應 schema staff_salary_summary
  clinic_id / service_month / employee_label / gross_salary / net_salary
  / paid_by_clinic_id / note

需求（院長 2026-05-03）：
  1. 自動辨識最新月份 sheet（如「薪資條115年03月」）；
     含「-更正」字尾的 sheet 優先（如「薪資條115年03月-更正」）
  2. 抓代付區塊：
     標題形如「115年01月澤豐薪資明細(澤沛代付)」
     → clinic_id=澤豐, paid_by_clinic_id=澤沛
     對應「總額」or「應付總額」金額（C8 對應同列）
  3. 抓一般員工區塊：
     標題形如「115年03月薪資明細」
     → clinic_id = 上傳者選的主聘診所, paid_by_clinic_id = NULL
     對應「總額」or「薪資B」or「總計」金額

員工 dedup key：(clinic_id, paid_by_clinic_id, service_month, name)
（同員工可能既有一般又有代付項目）

欄位語意（院長 2026-11-06 確認用語）：
  薪資一律由員工的「歸屬診所」（主聘）正式發放全月薪資；員工到另一家「服務診所」支援
  所產生的薪資應由服務診所負擔 → 歸屬診所先代付、服務診所事後還款（豐沛金流）。
  clinic_id          ＝ 服務診所（應負擔這筆薪資的診所）
  paid_by_clinic_id  ＝ 歸屬診所（正式發薪＝代付方；代付列才有值）
  自付列（一般員工）：clinic_id 即歸屬診所、paid_by 為 NULL。
  例：張郡紋歸屬澤豐、支援澤沛 → 澤沛檔右欄「115年09月澤沛薪資明細(澤豐代付)」
      → clinic=澤沛（服務）、paid_by=澤豐（歸屬），澤沛應給澤豐。

Excel 標示慣例（讓辨識不出錯；2026-11-06 檢視澤沛檔後整理）：
  1. sheet 名稱「薪資條115年10月」尾端**不要有空格**（程式已容忍，但請保持乾淨）。
  2. 跨院員工：左欄全月薪資計算用「N年M月薪資計算」當標題（不產出紀錄、只當區塊邊界）；
     右欄代付區塊用「N年M月{服務診所}薪資明細({歸屬診所}代付)」，金額列用「應付總額」。
  3. 區塊標題的年月必須與 sheet 月份相同，否則會被當成跨月舊資料略過
     （例：10 月 sheet 內張郡紋區塊標題仍寫 115年09月 → 10 月不會辨識到她）。
  4. 「姓名：XXX(到職日)」格式不變；括號前不要有空格。
"""

from __future__ import annotations

import re
from typing import IO

import pandas as pd


SHEET_RE = re.compile(r"^薪資條(\d{3})年(\d{1,2})月(-更正)?$")


def _match_sheet(sheet_name: str):
    """sheet 名稱比對（容忍前後空白／全形空白：澤沛檔「薪資條115年10月 」尾端多一個空格
    曾讓整個 10 月 sheet 被跳過，2026-11-06）。"""
    return SHEET_RE.match(str(sheet_name).replace("　", " ").strip())

# 標題（含「薪資明細」「薪資計算」皆視為區塊邊界）
# group 3=owner、group 4=payer；皆可選
# 支援兩種代付寫法：
#   1. 「XXX年XX月澤豐薪資明細(澤沛代付)」 — 完整 owner+payer
#   2. 「XXX年XX月薪資明細(澤豐代付)」     — 省略 owner（=檔案 default 診所）
# 「薪資計算」雖非員工最終區塊但可分區，用於正確切割員工範圍
TITLE_RE = re.compile(
    r"(\d{3})年(\d{1,2})月.*?"
    r"(?:(澤豐|澤沛)?薪資明細(?:\((澤豐|澤沛)代付\))?|薪資計算)"
)
# 「薪資計算」型標題（左欄常見，配合右欄代付區塊出現）— 不產出 records
SECTION_ONLY_RE = re.compile(r"(\d{3})年(\d{1,2})月.*?薪資計算")
NAME_RE = re.compile(r"姓名[:：]\s*([^\(\s]+)")

# 金額 label 優先序：
# 「總額」/「實領總額」是已扣勞健保的淨額（多數員工）
# 「薪資A」也是淨額（謝松坊這類「診薪+人數」結構員工）
# 「應付總額」用於代付區塊
# 「薪資B」是人數獎金（不用，會誤判）— 移除
AMOUNT_LABELS = ("總額", "實領總額", "薪資A", "應付總額", "總計")


def find_target_sheet(file_obj: IO) -> tuple[str, str]:
    """
    回傳 (sheet_name, service_month_iso)。
    自動辨識最新月份 sheet；同月份「-更正」優先。
    """
    file_obj.seek(0)
    xl = pd.ExcelFile(file_obj)
    candidates: list[tuple[int, int, bool, str]] = []
    for sn in xl.sheet_names:
        m = _match_sheet(sn)
        if not m:
            continue
        candidates.append((
            int(m.group(1)),
            int(m.group(2)),
            bool(m.group(3)),
            sn,
        ))
    if not candidates:
        raise ValueError("找不到「薪資條XXX年XX月」格式的 sheet")

    # 排序：年月 desc，同月份「-更正」優先
    candidates.sort(key=lambda x: (-x[0], -x[1], 0 if x[2] else 1))
    roc_y, roc_m, _, sn = candidates[0]
    service_month = f"{roc_y + 1911:04d}-{roc_m:02d}-01"
    return sn, service_month


def list_sheets(file_obj: IO) -> list[tuple[str, str]]:
    """
    列出檔案內所有合法薪資 sheet（同月份「-更正」優先取代非更正版本）。
    回傳 [(sheet_name, service_month_iso), ...]，按月份新到舊排序。
    """
    file_obj.seek(0)
    xl = pd.ExcelFile(file_obj)
    by_month: dict[tuple[int, int], tuple[bool, str]] = {}
    for sn in xl.sheet_names:
        m = _match_sheet(sn)
        if not m:
            continue
        roc_y = int(m.group(1))
        roc_m = int(m.group(2))
        corrected = bool(m.group(3))
        cur = by_month.get((roc_y, roc_m))
        if cur is None or (corrected and not cur[0]):
            by_month[(roc_y, roc_m)] = (corrected, sn)
    out = [
        (sn, f"{y + 1911:04d}-{mo:02d}-01")
        for (y, mo), (_, sn) in by_month.items()
    ]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def parse_staff_salary(
    file_obj: IO,
    source_filename: str,
    default_clinic_id: int,
    clinic_short_to_id: dict[str, int],
    target_sheet: str | None = None,
) -> tuple[str, list[dict]]:
    """
    解析員工薪資 xlsx → list[dict]（對應 staff_salary_summary）。
    target_sheet=None 時自動偵測最新；指定 sheet 名稱則只解析該 sheet。
    （需要略過原因診斷請用 parse_staff_salary_ex）
    """
    sheet_name, records, _diag = parse_staff_salary_ex(
        file_obj, source_filename, default_clinic_id, clinic_short_to_id, target_sheet
    )
    return sheet_name, records


def _find_name(df: pd.DataFrame, title: dict, next_r: int) -> str | None:
    """標題下 5 列內、同欄範圍找「姓名：xxx」。"""
    col_range = range(title["c"], min(title["c"] + 6, df.shape[1]))
    for r in range(title["r"] + 1, min(next_r, title["r"] + 5)):
        for cc in col_range:
            v = df.iloc[r, cc]
            if pd.isna(v):
                continue
            nm = NAME_RE.match(str(v).strip())
            if nm:
                return nm.group(1)
    return None


def parse_staff_salary_ex(
    file_obj: IO,
    source_filename: str,
    default_clinic_id: int,
    clinic_short_to_id: dict[str, int],
    target_sheet: str | None = None,
) -> tuple[str, list[dict], dict]:
    """
    同 parse_staff_salary，另回傳 diagnostics（給 UI 提示為什麼某人沒被辨識）：
      {
        "month_mismatch": [{"name", "title", "sheet_month"}],  # 標題年月 ≠ sheet 月份 → 略過
        "no_amount":      [{"name", "title"}],                 # 找不到總額/應付總額等金額列 → 略過
      }
    """
    if target_sheet is None:
        sheet_name, service_month = find_target_sheet(file_obj)
    else:
        m = _match_sheet(target_sheet)
        if not m:
            raise ValueError(f"sheet 名稱格式不符：{target_sheet}")
        sheet_name = target_sheet
        service_month = f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-01"
    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=sheet_name, header=None)

    diagnostics: dict[str, list[dict]] = {"month_mismatch": [], "no_amount": []}

    # 從 sheet 名取目標年月，用以過濾跨月舊資料
    sheet_m = _match_sheet(sheet_name)
    target_roc_y = int(sheet_m.group(1)) if sheet_m else None
    target_roc_m = int(sheet_m.group(2)) if sheet_m else None

    # 收集所有「薪資明細/薪資計算」標題作為員工區塊邊界
    # 跨月舊資料 (114/2~) + 薪資計算 都當邊界，但不產出 records
    titles: list[dict] = []
    for r in range(df.shape[0]):
        for c in range(df.shape[1]):
            v = df.iloc[r, c]
            if pd.isna(v):
                continue
            s = str(v).strip()
            m = TITLE_RE.search(s)
            if not m:
                continue
            t_y = int(m.group(1))
            t_m = int(m.group(2))
            is_target = (
                target_roc_y is not None
                and (t_y, t_m) == (target_roc_y, target_roc_m)
            )
            is_section_only = bool(SECTION_ONLY_RE.search(s))
            owner = m.group(3)        # 可能 None
            payer = m.group(4)        # 可能 None
            # is_daifu: 有 (XX代付) 即視為跨代付，owner 可省略
            is_daifu = bool(payer)
            titles.append({
                "r": r, "c": c,
                "text": s,
                "owner": owner,
                "payer": payer,
                "is_daifu": is_daifu,
                "is_target": is_target,
                "is_section_only": is_section_only,
                "skip_record": is_section_only or not is_target,
            })
    titles.sort(key=lambda t: (t["r"], t["c"]))

    records: list[dict] = []
    seen: set[tuple] = set()

    for i, title in enumerate(titles):
        c = title["c"]
        col_range = range(c, min(c + 6, df.shape[1]))

        # 找下個同欄位範圍內的標題（決定本員工區塊終點）— 含 skip_record 的也算邊界
        next_r = df.shape[0]
        for nt in titles[i + 1:]:
            if nt["c"] in col_range and nt["r"] > title["r"]:
                next_r = nt["r"]
                break

        if title["skip_record"]:
            # 跨月舊資料 → 記診斷（有姓名才記，避免雜訊）；薪資計算 → 只當邊界用
            if not title["is_target"] and not title["is_section_only"]:
                nm = _find_name(df, title, next_r)
                if nm:
                    diagnostics["month_mismatch"].append({
                        "name": nm, "title": title["text"], "sheet_month": service_month,
                    })
            continue

        # 抓姓名（範圍內第一個「姓名：xxx」）
        name = _find_name(df, title, next_r)
        if not name:
            continue

        # 決定 clinic / paid_by
        if title["is_daifu"]:
            # owner 可能省略（如「薪資明細(澤豐代付)」）→ 取 default
            owner = title["owner"]
            payer = title["payer"]
            clinic_id = (
                clinic_short_to_id.get(owner) if owner else default_clinic_id
            )
            paid_by_id = clinic_short_to_id.get(payer)
            if clinic_id is None or paid_by_id is None:
                continue
            # 若 owner = payer（自付而非代付）退回普通歸屬
            if clinic_id == paid_by_id:
                paid_by_id = None
        else:
            clinic_id = default_clinic_id
            paid_by_id = None

        # 抓金額（按優先序找「總額」等 label）
        amount = None
        for label in AMOUNT_LABELS:
            for r in range(title["r"] + 1, next_r):
                for cc in col_range:
                    v = df.iloc[r, cc]
                    if pd.isna(v):
                        continue
                    if str(v).strip() == label:
                        nv = df.iloc[r, cc + 1] if cc + 1 < df.shape[1] else None
                        if pd.notna(nv):
                            try:
                                amount = int(float(nv))
                                break
                            except (ValueError, TypeError):
                                pass
                if amount is not None:
                    break
            if amount is not None:
                break

        if amount is None:
            diagnostics["no_amount"].append({"name": name, "title": title["text"]})
            continue

        key = (clinic_id, paid_by_id, service_month, name)
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "clinic_id": clinic_id,
            "service_month": service_month,
            "employee_label": name,
            "gross_salary": amount,
            "net_salary": amount,
            "paid_by_clinic_id": paid_by_id,
            "note": "跨診所代付" if paid_by_id else None,
        })

    return sheet_name, records, diagnostics
