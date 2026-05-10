"""
合理門診量匯入處理（11503+）

對應 schema doctor_capacity_stage

檔案結構（每位主聘醫師一個區塊）：
  R0: 機構碼 + 診所名
  R1: "115年MM月XX合理就醫日統計表"
  R2: 列印日期
  R3: 空
  R(n)   專任醫師區塊頭：
         C0='專任醫師看診天數：N'
         C3='看診總人次：N'
         C4='專任醫師：醫師姓名 D12179'
  R(n+1) 表頭+meta：
         C0='階段' C1='合理人數' C2='實際人次'
         C3='實際申報人次：N'
         C4='補支援醫師數：N'  （可選；無則 0）
  R(n+2..n+6) 5 階段資料：
         C0='1-30人次' / '31-50人次' / '51-70人次' / '71-150人次' / '151-1000人次'
         C1=合理人數
         C2=實際人次

每位醫師之間用空行分隔。

演算法：
  1. 抽每位主聘醫師 stage_actual[1..5] (= C2) + support_offset (= R+1 C4)
  2. 從 stage5 往前扣 support_offset，得分配後 stage[1..5] + 被扣量 deduction[1..5]
  3. 該診所支援醫師 = 各正職 deduction 加總（同階段加總）
  4. 支援醫師身份從 doctor_clinic.role='support' 該診所配置
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import IO

import pandas as pd


FILENAME_RE = re.compile(r"^(\d{5})(澤豐|澤沛)合理門診量\.xlsx$")
# 檔上是「專任醫生」（非「醫師」）；醫師代號可能 D12179 / D121791297 等格式
DOCTOR_RE = re.compile(r"專任醫[師生][:：]\s*(\S+?)\s+[A-Za-z]\d+")
SUPPORT_OFFSET_RE = re.compile(r"補支援醫[師生]?數[:：]\s*(\d+)")
STAGE_LABELS = ("1-30人次", "31-50人次", "51-70人次", "71-150人次", "151-1000人次")


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


def parse_filename(filename: str) -> dict:
    """檔名 → 服務月份 + 診所"""
    name = Path(filename).name
    m = FILENAME_RE.match(name)
    if not m:
        raise ValueError(f"檔名格式不符「YYYYMM{{澤豐|澤沛}}合理門診量.xlsx」：{name}")
    yyyymm = m.group(1)
    clinic_short = m.group(2)
    roc_y, roc_m = int(yyyymm[:3]), int(yyyymm[3:])
    return {
        "yyyymm": yyyymm,
        "clinic_short": clinic_short,
        "service_month": f"{roc_y + 1911:04d}-{roc_m:02d}-01",
    }


def _distribute_offset(stages: list[int], offset: int) -> tuple[list[int], list[int]]:
    """從末階段往前扣 offset。
    回傳 (分配後 stages, 被扣量 deduction)。各 list 長度同 stages。
    若 offset 大於 stages 總和，扣到全 0 後剩餘部分丟棄（不會變負）。
    """
    distributed = list(stages)
    deduction = [0] * len(stages)
    remaining = offset
    for i in range(len(stages) - 1, -1, -1):
        if remaining <= 0:
            break
        take = min(distributed[i], remaining)
        distributed[i] -= take
        deduction[i] = take
        remaining -= take
    return distributed, deduction


def parse_capacity_quota(
    file_obj: IO,
    source_filename: str,
    clinic_id: int,
    name_to_doctor_id: dict[str, int],
    support_doctor_id: int | None,
) -> tuple[list[dict], dict]:
    """解析合理門診量 xlsx。

    Args:
        support_doctor_id: 該診所支援醫師 doctor_id（從 doctor_clinic.role='support'
                           找；可能 None，表示沒配置 → 所有 deduction 無法歸屬）

    Returns:
        (records, meta) — records 對應 doctor_capacity_stage 一個 row 一個醫師
        含主聘 + （若有支援配置）支援
    """
    meta = parse_filename(source_filename)
    file_obj.seek(0)
    df = pd.read_excel(file_obj, sheet_name=0, header=None)

    records: list[dict] = []
    unknown: list[str] = []
    aggregated_deduction = [0, 0, 0, 0, 0]
    total_offset = 0

    r = 0
    while r < df.shape[0]:
        c4 = df.iloc[r, 4] if df.shape[1] > 4 else None
        if pd.notna(c4) and ("專任醫師" in str(c4) or "專任醫生" in str(c4)):
            m = DOCTOR_RE.search(str(c4))
            if not m:
                r += 1
                continue
            name = m.group(1).strip()
            r1 = r + 1
            if r1 >= df.shape[0]:
                break
            offset = 0
            c4_meta = df.iloc[r1, 4] if df.shape[1] > 4 else None
            if pd.notna(c4_meta):
                m2 = SUPPORT_OFFSET_RE.search(str(c4_meta))
                if m2:
                    offset = int(m2.group(1))

            stages = [0] * 5
            for i in range(5):
                rr = r1 + 1 + i
                if rr >= df.shape[0]:
                    break
                label = df.iloc[rr, 0]
                if pd.isna(label) or str(label).strip() != STAGE_LABELS[i]:
                    break
                stages[i] = _to_int(df.iloc[rr, 2])

            distributed, deduction = _distribute_offset(stages, offset)
            for i in range(5):
                aggregated_deduction[i] += deduction[i]
            total_offset += offset

            if name not in name_to_doctor_id:
                unknown.append(name)
            else:
                records.append({
                    "clinic_id": clinic_id,
                    "doctor_id": name_to_doctor_id[name],
                    "service_month": meta["service_month"],
                    "stage1": distributed[0],
                    "stage2": distributed[1],
                    "stage3": distributed[2],
                    "stage4": distributed[3],
                    "stage5": distributed[4],
                    "is_support": False,
                    "support_offset": offset,
                    "source_filename": source_filename,
                })

            r = r1 + 6
            continue
        r += 1

    if total_offset > 0 and support_doctor_id is not None:
        records.append({
            "clinic_id": clinic_id,
            "doctor_id": support_doctor_id,
            "service_month": meta["service_month"],
            "stage1": aggregated_deduction[0],
            "stage2": aggregated_deduction[1],
            "stage3": aggregated_deduction[2],
            "stage4": aggregated_deduction[3],
            "stage5": aggregated_deduction[4],
            "is_support": True,
            "support_offset": 0,
            "source_filename": source_filename,
        })

    meta["unknown_doctors"] = unknown
    meta["total_offset"] = total_offset
    meta["aggregated_deduction"] = aggregated_deduction
    meta["has_support_attribution"] = support_doctor_id is not None
    return records, meta
