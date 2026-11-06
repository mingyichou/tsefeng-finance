"""
國定假日行事曆 ＋ 醫師國定假日給薪（v16）

院長 2026-11-06 需求：115/10 到職的蔣慈芳醫師約定「特定國定假日額外薪資」。
每位醫師獨立設定 16 個節日的「每日薪資」（預設 0＝不給薪）；
算薪時把該月內有給薪的國定假日逐一列出、加總，計入該醫師主聘診所的應付薪資。

日期認定方式（設計決策，不上網抓取）：
  - 16 個節日都是「規則可算」：7 個固定公曆日 ＋ 9 個農曆／節氣日。
  - 農曆日期用 lunardate（純 Python 農曆表，1900–2099）換算；
    清明用天文近似公式（本世紀落在 4/4 或 4/5）：
      日 = [Y×0.2422 ＋ 4.81] − [Y/4]，Y＝西元年末兩位（2001–2100 適用）。
  - 兒童節依《紀念日及節日實施辦法》：4/4 與清明同日時提前至 4/3；但 4/4 逢週四則延至 4/5。
  - 「系統設定 → 國定假日給薪」分頁會列出每年 16 個節日的計算日期；
    若政府公告與計算不同，可在該分頁覆寫單一年度的任一節日日期
    （存 system_settings.holiday_date_overrides，JSON：{"2027": {"tomb_sweeping": "2027-04-05"}}）。
  - 月份歸屬＝節日實際日期所在月：春節三天若跨月（如 2033 年初一 1/31、初二 2/1），
    各自落在所屬月份，這也是 16 個節日分列設定的原因。

資料表：
  doctor_holiday_pay (doctor_id, holiday_key, amount)  — 每醫師每節日每日薪資
  doctor_salary_monthly.holiday_pay / holiday_breakdown — 當月國定假日薪資（主聘列）
"""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

try:  # 純 Python 農曆表（requirements.txt: lunardate）
    from lunardate import LunarDate as _LunarDate
    LUNAR_AVAILABLE = True
except Exception:  # pragma: no cover - 未安裝時農曆節日無法計算
    _LunarDate = None
    LUNAR_AVAILABLE = False


# ─── 16 個節日定義（key 為 DB 欄位值，順序＝設定頁顯示順序）──────────
# (key, 名稱, 類型)：fixed＝固定公曆；lunar＝農曆／節氣（日期逐年變動）
HOLIDAY_DEFS: list[tuple[str, str, str]] = [
    ("new_year",          "元旦（開國紀念日）",               "fixed"),
    ("peace_day",         "和平紀念日",                       "fixed"),
    ("labor_day",         "勞動節",                           "fixed"),
    ("teachers_day",      "孔子誕辰紀念日（教師節）",         "fixed"),
    ("national_day",      "國慶日",                           "fixed"),
    ("retrocession_day",  "臺灣光復暨金門古寧頭大捷紀念日",   "fixed"),
    ("constitution_day",  "行憲紀念日",                       "fixed"),
    ("lunar_eve_eve",     "小年夜",                           "lunar"),
    ("lunar_eve",         "農曆除夕",                         "lunar"),
    ("cny_1",             "春節（初一）",                     "lunar"),
    ("cny_2",             "春節（初二）",                     "lunar"),
    ("cny_3",             "春節（初三）",                     "lunar"),
    ("children_day",      "兒童節",                           "lunar"),
    ("tomb_sweeping",     "民族掃墓節（清明）",               "lunar"),
    ("dragon_boat",       "端午節",                           "lunar"),
    ("mid_autumn",        "中秋節",                           "lunar"),
]
HOLIDAY_KEYS: tuple[str, ...] = tuple(k for k, _, _ in HOLIDAY_DEFS)
HOLIDAY_LABEL: dict[str, str] = {k: label for k, label, _ in HOLIDAY_DEFS}
HOLIDAY_GROUP: dict[str, str] = {k: g for k, _, g in HOLIDAY_DEFS}
GROUP_LABEL = {"fixed": "固定公曆日期", "lunar": "變動農曆／民俗日期"}

FIXED_MD: dict[str, tuple[int, int]] = {
    "new_year": (1, 1),
    "peace_day": (2, 28),
    "labor_day": (5, 1),
    "teachers_day": (9, 28),
    "national_day": (10, 10),
    "retrocession_day": (10, 25),
    "constitution_day": (12, 25),
}

OVERRIDES_SETTING_KEY = "holiday_date_overrides"
WEEKDAY_ZH = "一二三四五六日"


# ─── 日期計算 ────────────────────────────────────────────────


def _lunar_to_solar(year: int, month: int, day: int) -> date | None:
    """農曆 → 公曆；lunardate 未安裝或超出範圍回 None。"""
    if not LUNAR_AVAILABLE:
        return None
    try:
        ld = _LunarDate(year, month, day)
        conv = getattr(ld, "to_solar_date", None) or getattr(ld, "toSolarDate")
        return conv()
    except Exception:
        return None


def qingming_day(year: int) -> date:
    """清明（節氣）日期：本世紀近似公式 [Y×0.2422＋4.81]−[Y/4]，Y＝西元末兩位。
    2001–2100 間誤差極少；若遇官方公告不同，於設定頁覆寫該年日期即可。"""
    y = year % 100
    d = int(y * 0.2422 + 4.81) - int(y / 4)
    return date(year, 4, d)


def children_day(year: int, qingming: date) -> date:
    """兒童節 4/4；與清明同日 → 4/3，但 4/4 逢週四 → 4/5（紀念日及節日實施辦法）。"""
    d = date(year, 4, 4)
    if qingming == d:
        return date(year, 4, 5) if d.weekday() == 3 else date(year, 4, 3)
    return d


def computed_holiday_dates(year: int) -> dict[str, date | None]:
    """純計算（不含覆寫）：{key: date | None}；農曆庫不可用時農曆節日為 None。"""
    out: dict[str, date | None] = {}
    for k, (m, d) in FIXED_MD.items():
        out[k] = date(year, m, d)
    cny = _lunar_to_solar(year, 1, 1)
    out["cny_1"] = cny
    out["cny_2"] = cny + timedelta(days=1) if cny else None
    out["cny_3"] = cny + timedelta(days=2) if cny else None
    out["lunar_eve"] = cny - timedelta(days=1) if cny else None
    out["lunar_eve_eve"] = cny - timedelta(days=2) if cny else None
    qm = qingming_day(year)
    out["tomb_sweeping"] = qm
    out["children_day"] = children_day(year, qm)
    out["dragon_boat"] = _lunar_to_solar(year, 5, 5)
    out["mid_autumn"] = _lunar_to_solar(year, 8, 15)
    return out


def _parse_override(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def holiday_dates(year: int, overrides: dict | None = None) -> list[dict]:
    """該年 16 個節日（依 HOLIDAY_DEFS 順序）：
    [{key, label, group, date, computed, overridden}]；date 可能為 None（無法計算且未覆寫）。
    overrides 格式：{"2027": {"tomb_sweeping": "2027-04-05", ...}}（年份鍵為字串或整數皆可）。"""
    computed = computed_holiday_dates(year)
    yr_over = {}
    if overrides:
        yr_over = overrides.get(str(year)) or overrides.get(year) or {}
    out = []
    for k, label, group in HOLIDAY_DEFS:
        ov = _parse_override(yr_over.get(k)) if isinstance(yr_over, dict) else None
        out.append({
            "key": k, "label": label, "group": group,
            "date": ov or computed.get(k),
            "computed": computed.get(k),
            "overridden": ov is not None,
        })
    return out


def holidays_in_month(service_month: str, overrides: dict | None = None) -> list[dict]:
    """落在該月（'YYYY-MM-01'）的節日清單（含日期）。"""
    start = date.fromisoformat(str(service_month)[:10]).replace(day=1)
    last = calendar.monthrange(start.year, start.month)[1]
    end = date(start.year, start.month, last)
    return [h for h in holiday_dates(start.year, overrides)
            if h["date"] and start <= h["date"] <= end]


def fmt_holiday_date(d: date | None) -> str:
    """4/4（週六）"""
    if not d:
        return "—"
    return f"{d.month}/{d.day}（週{WEEKDAY_ZH[d.weekday()]}）"


# ─── 醫師給薪設定 ───────────────────────────────────────────


def fetch_holiday_pay(sb) -> dict[int, dict[str, int]]:
    """doctor_holiday_pay → {doctor_id: {holiday_key: amount}}；表不存在（migration 未跑）回 {}。"""
    try:
        rows = sb.table("doctor_holiday_pay").select(
            "doctor_id, holiday_key, amount"
        ).execute().data or []
    except Exception:
        return {}
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        amt = int(r.get("amount") or 0)
        if amt:
            out.setdefault(int(r["doctor_id"]), {})[str(r["holiday_key"])] = amt
    return out


def fetch_holiday_overrides(sb) -> dict:
    """system_settings.holiday_date_overrides（JSON）；無／壞 → {}。"""
    try:
        rows = sb.table("system_settings").select("value").eq(
            "key", OVERRIDES_SETTING_KEY
        ).limit(1).execute().data
    except Exception:
        return {}
    if not rows:
        return {}
    v = rows[0].get("value")
    if isinstance(v, dict):
        return v
    try:
        parsed = json.loads(v) if v else {}
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def holiday_pay_for_month(
    amounts: dict[str, int] | None,
    service_month: str,
    overrides: dict | None = None,
) -> tuple[int, list[dict]]:
    """
    該醫師該月國定假日薪資：只列「落在該月」且「每日薪資 > 0」的節日。
    Returns (合計, [{key, label, date(ISO), amount}])；沒有任何給薪 → (0, [])。
    """
    if not amounts:
        return 0, []
    items: list[dict] = []
    for h in holidays_in_month(service_month, overrides):
        amt = int(amounts.get(h["key"]) or 0)
        if amt > 0:
            items.append({
                "key": h["key"], "label": h["label"],
                "date": h["date"].isoformat(), "amount": amt,
            })
    return sum(i["amount"] for i in items), items
