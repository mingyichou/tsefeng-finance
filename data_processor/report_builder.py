"""
財報列印 — 報表產生引擎

產出「自帶 CSS、可列印成 A4」的獨立 HTML 報表字串（1~2 頁）。
趨勢圖用純 Python 產生的內嵌 SVG（離線可印，不依賴外部套件/CDN）。

報表種類：
  A 月度實帳金流分析（單診所）
  B 季度實帳金流分析（單診所）
  C 年度實帳金流分析（單診所）
  D 月度損益分析（單診所）
  E 年度損益分析（單診所）

完整度把關：
  - 實帳金流(A/B/C)：用 data_health.compute_cashflow_health 的該診所 issues；
    期間內任一月份不完整 → 不產生報表，回傳註記。
  - 損益(D/E)：月份 G(總收入) 或 H(薪資合計) 為 0 視為不完整。
  - 趨勢圖只納入「有效」期間（季/年需其所有月份皆完整）。
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass
from datetime import date

BRAND = "#6A5ACD"
GREEN = "#5CB85C"
RED = "#D9534F"
BLUE = "#4A90E2"

MIN_MONTH = "2026-01-01"


# ════════════════════════════════════════════════════════════
# 月份 / 季 / 年 工具
# ════════════════════════════════════════════════════════════

def _add_months(month_iso: str, n: int) -> str:
    d = date.fromisoformat(month_iso)
    y, m = d.year, d.month + n
    while m > 12:
        m -= 12; y += 1
    while m < 1:
        m += 12; y -= 1
    return date(y, m, 1).isoformat()


def quarter_of(month_iso: str) -> tuple[int, int]:
    d = date.fromisoformat(month_iso)
    return d.year, (d.month - 1) // 3 + 1


def months_of_quarter(year: int, q: int) -> list[str]:
    start = (q - 1) * 3 + 1
    return [f"{year:04d}-{start + i:02d}-01" for i in range(3)]


def months_of_year(year: int) -> list[str]:
    return [f"{year:04d}-{m:02d}-01" for m in range(1, 13)]


def roc_label(month_iso: str) -> str:
    d = date.fromisoformat(month_iso)
    return f"{d.year}-{d.month:02d}"


# ════════════════════════════════════════════════════════════
# 數字格式
# ════════════════════════════════════════════════════════════

def _fmt(v) -> str:
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return "—"


def _fmt_wan(v: float) -> str:
    """軸刻度用：>= 1 萬以「萬」顯示。"""
    av = abs(v)
    if av >= 10000:
        return f"{v / 10000:.1f}萬"
    return f"{int(round(v)):,}"


# ════════════════════════════════════════════════════════════
# 資料快取（避免重複查 DB）
# ════════════════════════════════════════════════════════════

class CashflowData:
    """單月實帳金流 + 完整度，依診所取用。"""

    def __init__(self, sb):
        self.sb = sb
        self._pl: dict = {}
        self._health: dict = {}

    def _ensure(self, month: str):
        if month not in self._pl:
            from data_processor.monthly_pl import calculate_both_clinics
            self._pl[month] = calculate_both_clinics(self.sb, month)
        return self._pl[month]

    def clinic_pl(self, month: str, clinic: str):
        fz, fp = self._ensure(month)
        return fz if clinic == "澤豐" else fp

    def issues(self, month: str, clinic: str) -> list[str]:
        if month not in self._health:
            from data_processor.data_health import compute_cashflow_health
            self._health[month] = compute_cashflow_health(self.sb, month)
        h = self._health[month]
        return h["issues_fz"] if clinic == "澤豐" else h["issues_fp"]

    def complete(self, month: str, clinic: str) -> bool:
        return not self.issues(month, clinic)


class PLData:
    """單月損益（會計精神），依診所取用。"""

    def __init__(self, sb):
        self.sb = sb
        self._pl: dict = {}
        self._health: dict = {}

    def _ensure(self, month: str):
        if month not in self._pl:
            from data_processor.monthly_profit_loss import calculate_both_pl
            self._pl[month] = calculate_both_pl(self.sb, month)
        return self._pl[month]

    def clinic_pl(self, month: str, clinic: str):
        fz, fp = self._ensure(month)
        return fz if clinic == "澤豐" else fp

    def complete(self, month: str, clinic: str) -> bool:
        # 完整度依「下個月銀行 CSV + 本月健保第一筆給付」上游資料判定
        # （比跑完整 calculate_both_pl 便宜，趨勢掃描更快）。
        if month not in self._health:
            from data_processor.data_health import compute_pl_health
            self._health[month] = compute_pl_health(self.sb, month)
        h = self._health[month]
        return h["complete_fz"] if clinic == "澤豐" else h["complete_fp"]


# ════════════════════════════════════════════════════════════
# 分類細項
# ════════════════════════════════════════════════════════════

def cashflow_income_rows(clinic: str, pl) -> list[tuple]:
    if clinic == "澤豐":
        return [
            ("玉山逐筆入帳", pl.esun_inflow_total),
            ("豐沛金流（澤沛→澤豐）", pl.x6_fengpei_settle),
            ("澤豐現金入帳", pl.x8_zefeng_cash_revenue),
            ("手 KEY 收入", pl.x10_income_total),
        ]
    return [
        ("玉山逐筆入帳", pl.esun_inflow_total),
        ("中信進出戶入帳", pl.ctbc_inflow_total),
        ("手 KEY 收入", pl.x10_income_total),
    ]


def cashflow_expense_rows(clinic: str, pl) -> list[tuple]:
    if clinic == "澤豐":
        rows = [
            ("玉山逐筆出帳", pl.esun_outflow_total),
            ("澤豐現金支出", pl.x3_zefeng_cash_expense),
            ("謝松坊薪資", pl.x9_offsite_staff_pay),
            ("手 KEY 支出", pl.x10_expense_total),
            ("澤豐合約支出", pl.x12_zefeng_contract_expense),
            ("周院長薪資（兩院總和）", pl.x13_zhou_doctor_salary),
        ]
        if getattr(pl, "x14_cash_salary_pay", 0):
            rows.append(("醫師薪資現金給付", pl.x14_cash_salary_pay))
        return rows
    # 澤沛：中信出帳含 3 筆結算，逐項列出（縮排子項，僅供股東對帳識別，不重複加總）
    rows = [
        ("玉山逐筆出帳", pl.esun_outflow_total),
        ("中信進出戶出帳（含結算）", pl.ctbc_outflow_total),
        ("現金結算（→周院長）", pl.cash_settle_outflow, True),
        ("豐沛金流（→澤豐）", pl.fengpei_outflow, True),
        ("合約結算（→周院長）", pl.contract_settle_outflow, True),
        ("手 KEY 支出", pl.x10_expense_total),
    ]
    if getattr(pl, "cash_salary_pay_total", 0):
        rows.append(("醫師薪資現金給付", pl.cash_salary_pay_total))
    return rows


# ════════════════════════════════════════════════════════════
# 內嵌 SVG 折線圖（可印；支援負值；多序列）
# ════════════════════════════════════════════════════════════

@dataclass
class Series:
    name: str
    values: list  # 與 x_labels 對齊，可含 None
    color: str = BRAND


def svg_line_chart(
    x_labels: list[str], series: list[Series], *,
    width: int = 660, height: int = 250, title: str = "",
) -> str:
    ml, mr, mt, mb = 64, 18, 30 if title else 12, 46
    pw = width - ml - mr
    ph = height - mt - mb
    n = len(x_labels)

    vals = [v for s in series for v in s.values if v is not None]
    if not vals:
        return (f'<div class="chart-empty">{_html.escape(title)}：'
                "無有效資料點</div>")
    vmax = max(vals + [0])
    vmin = min(vals + [0])
    if vmax == vmin:
        vmax += 1
    pad = (vmax - vmin) * 0.08 or 1
    vmax += pad
    vmin -= pad if vmin < 0 else 0

    def x_at(i: int) -> float:
        if n == 1:
            return ml + pw / 2
        return ml + pw * i / (n - 1)

    def y_at(v: float) -> float:
        return mt + ph * (vmax - v) / (vmax - vmin)

    parts: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" class="trend-svg">'
    ]
    if title:
        parts.append(
            f'<text x="{width/2:.0f}" y="16" text-anchor="middle" '
            f'class="svg-title">{_html.escape(title)}</text>'
        )

    # y 軸格線 + 刻度（4 段）
    for k in range(5):
        v = vmin + (vmax - vmin) * k / 4
        y = y_at(v)
        parts.append(
            f'<line x1="{ml}" y1="{y:.1f}" x2="{width-mr}" y2="{y:.1f}" '
            'class="grid"/>'
        )
        parts.append(
            f'<text x="{ml-6}" y="{y+3:.1f}" text-anchor="end" '
            f'class="axis">{_fmt_wan(v)}</text>'
        )
    # 0 基準線（有負值時凸顯）
    if vmin < 0 < vmax:
        y0 = y_at(0)
        parts.append(
            f'<line x1="{ml}" y1="{y0:.1f}" x2="{width-mr}" y2="{y0:.1f}" '
            'class="zero"/>'
        )

    # x 軸標籤
    for i, lab in enumerate(x_labels):
        parts.append(
            f'<text x="{x_at(i):.1f}" y="{height-mb+18}" '
            f'text-anchor="middle" class="axis">{_html.escape(lab)}</text>'
        )

    # 各序列折線 + 點
    for s in series:
        pts = [(x_at(i), y_at(v)) for i, v in enumerate(s.values)
               if v is not None]
        if len(pts) >= 2:
            d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)
            parts.append(f'<path d="{d}" fill="none" stroke="{s.color}" '
                         'stroke-width="2.5"/>')
        for (x, y), v in zip(pts, [v for v in s.values if v is not None]):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" '
                         f'fill="{s.color}"/>')
            parts.append(
                f'<text x="{x:.1f}" y="{y-7:.1f}" text-anchor="middle" '
                f'class="pt">{_fmt_wan(v)}</text>'
            )

    # 圖例（多序列時）
    if len(series) > 1:
        lx = ml
        for s in series:
            parts.append(f'<rect x="{lx}" y="{height-14}" width="12" '
                         f'height="6" fill="{s.color}"/>')
            parts.append(f'<text x="{lx+16}" y="{height-9}" '
                         f'class="legend">{_html.escape(s.name)}</text>')
            lx += 16 + len(s.name) * 13 + 18

    parts.append("</svg>")
    return "".join(parts)


# ════════════════════════════════════════════════════════════
# HTML 文件骨架（A4 列印）
# ════════════════════════════════════════════════════════════

_CSS = """
* { box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important; }
@page { size: A4 portrait; margin: 12mm; }
body { font-family: "Microsoft JhengHei","PingFang TC","Noto Sans TC",
       sans-serif; color:#222; margin:0; padding:16px; font-size:13px; }
.report { max-width: 760px; margin:0 auto; }
h1 { color:%(brand)s; font-size:22px; margin:0 0 2px; }
.subtitle { color:#555; font-size:13px; margin:0 0 12px; }
.kpis { display:flex; gap:10px; margin:10px 0 14px; }
.kpi { flex:1; border:1px solid #e3e0f3; border-radius:8px; padding:10px 12px;
       background:#faf9ff; }
.kpi .label { font-size:11px; color:#666; }
.kpi .value { font-size:19px; font-weight:700; margin-top:3px; }
.kpi .value.pos { color:%(green)s; } .kpi .value.neg { color:%(red)s; }
h2 { font-size:15px; color:%(brand)s; border-bottom:2px solid #e3e0f3;
     padding-bottom:3px; margin:16px 0 8px; }
table { width:100%%; border-collapse:collapse; margin:6px 0 10px;
        font-size:12.5px; }
th,td { border:1px solid #e0e0e0; padding:5px 8px; }
th { background:#f3f1fb; text-align:left; font-weight:600; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
tr.total td { font-weight:700; background:#FFF8DC; color:#7a5b00; }
tr.sub td { color:#888; font-size:11.5px; background:#fafafa; }
.subnote { color:#999; font-size:10.5px; margin:-6px 0 10px; }
.trend-svg { width:100%%; height:auto; }
.svg-title { font-size:13px; font-weight:700; fill:#333; }
.axis { font-size:10px; fill:#888; } .pt { font-size:9.5px; fill:#444; }
.legend { font-size:10px; fill:#555; }
.grid { stroke:#eee; stroke-width:1; } .zero { stroke:#bbb; stroke-width:1.2; }
.chart-empty { color:#999; font-size:12px; padding:18px; text-align:center;
       border:1px dashed #ddd; border-radius:6px; }
.note { background:#fff6e5; border:1px solid #ffd591; border-radius:6px;
        padding:10px 12px; color:#8a5a00; font-size:12.5px; margin:8px 0; }
.foot { margin-top:18px; border-top:1px solid #eee; padding-top:6px;
        color:#999; font-size:10.5px; }
@media print { body { padding:0; } .no-print { display:none; } }
""" % {"brand": BRAND, "green": GREEN, "red": RED}


def _doc(title: str, subtitle: str, body: str, generated: str) -> str:
    return (
        "<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>"
        f"<title>{_html.escape(title)}</title><style>{_CSS}</style></head>"
        "<body><div class='report'>"
        f"<h1>{_html.escape(title)}</h1>"
        f"<p class='subtitle'>{_html.escape(subtitle)}</p>"
        f"{body}"
        f"<div class='foot'>澤豐聯盟財務系統 · 報表產生時間 {generated}　"
        "（瀏覽器 Ctrl+P 可列印為 A4）</div>"
        "</div></body></html>"
    )


def _kpis(items: list[tuple[str, int, bool]]) -> str:
    """items: (label, value, is_profit)"""
    cells = []
    for label, value, is_profit in items:
        cls = ""
        if is_profit:
            cls = " pos" if value >= 0 else " neg"
        cells.append(
            f"<div class='kpi'><div class='label'>{_html.escape(label)}</div>"
            f"<div class='value{cls}'>NT$ {_fmt(value)}</div></div>"
        )
    return f"<div class='kpis'>{''.join(cells)}</div>"


def _cat_table(header: str, rows: list[tuple],
               total_label: str, total: int) -> str:
    """rows: (name, value) 或 (name, value, is_sub)。is_sub 子項縮排、不參與加總。"""
    cells = []
    has_sub = False
    for row in rows:
        name, value = row[0], row[1]
        is_sub = len(row) > 2 and row[2]
        if is_sub:
            has_sub = True
            cells.append(
                f"<tr class='sub'><td>　└ {_html.escape(name)}</td>"
                f"<td class='num'>{_fmt(value)}</td></tr>"
            )
        else:
            cells.append(
                f"<tr><td>{_html.escape(name)}</td>"
                f"<td class='num'>{_fmt(value)}</td></tr>"
            )
    note = ("<div class='subnote'>　└ 子項已含於上層，僅供對帳識別、不重複加總。"
            "</div>" if has_sub else "")
    return (
        f"<table><thead><tr><th>{_html.escape(header)}</th>"
        "<th style='width:160px;text-align:right'>金額 (NT$)</th></tr></thead>"
        f"<tbody>{''.join(cells)}"
        f"<tr class='total'><td>{_html.escape(total_label)}</td>"
        f"<td class='num'>{_fmt(total)}</td></tr></tbody></table>{note}"
    )


def _month_breakdown_table(month_labels: list[str], income: list[int],
                           expense: list[int], net: list[int],
                           net_label: str = "淨利") -> str:
    head = "<tr><th>月份</th>" + "".join(
        f"<th style='text-align:right'>{_html.escape(m)}</th>"
        for m in month_labels
    ) + "<th style='text-align:right'>合計</th></tr>"

    def _row(label, vals, cls=""):
        cells = "".join(f"<td class='num'>{_fmt(v)}</td>" for v in vals)
        tot = f"<td class='num'>{_fmt(sum(vals))}</td>"
        return f"<tr class='{cls}'><td>{label}</td>{cells}{tot}</tr>"

    body = (_row("收入", income) + _row("支出", expense)
            + _row(net_label, net, "total"))
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


# ════════════════════════════════════════════════════════════
# 有效期間搜尋（趨勢用）
# ════════════════════════════════════════════════════════════

def _valid_months_desc(all_months: list[str], is_complete, upto: str | None):
    out = []
    for m in all_months:  # all_months 為 desc
        if upto and m > upto:
            continue
        if is_complete(m):
            out.append(m)
    return out


def _valid_quarters(all_months_set: set, is_complete, upto_q, n: int):
    """回傳近 n 個「3 月皆存在且完整」的季 (year,q)，升冪。"""
    res = []
    y, q = upto_q
    while len(res) < n and (y, q) >= (2026, 1):
        ms = months_of_quarter(y, q)
        if all(m in all_months_set for m in ms) and all(is_complete(m) for m in ms):
            res.append((y, q))
        q -= 1
        if q == 0:
            q = 4; y -= 1
    return list(reversed(res))


def _valid_years(all_months_set: set, is_complete, upto_year: int, n: int):
    """回傳近 n 個「該年所有已存在月份皆完整」的年，升冪。"""
    res = []
    y = upto_year
    while len(res) < n and y >= 2026:
        exist = [m for m in months_of_year(y) if m in all_months_set]
        if exist and all(is_complete(m) for m in exist):
            res.append(y)
        y -= 1
    return list(reversed(res))


# ════════════════════════════════════════════════════════════
# 報表 A：月度實帳金流分析
# ════════════════════════════════════════════════════════════

def build_cashflow_monthly(sb, clinic: str, month: str,
                           all_months: list[str], generated: str):
    cf = CashflowData(sb)
    issues = cf.issues(month, clinic)
    if issues:
        return None, _incomplete_note([roc_label(month)], issues)

    pl = cf.clinic_pl(month, clinic)
    inc = cashflow_income_rows(clinic, pl)
    exp = cashflow_expense_rows(clinic, pl)

    body = _kpis([
        ("總收入", pl.total_income, False),
        ("總支出", pl.total_expense, False),
        ("月度淨利", pl.net, True),
    ])
    body += "<h2>收入分類細項</h2>" + _cat_table("收入項目", inc, "總收入", pl.total_income)
    body += "<h2>支出分類細項</h2>" + _cat_table("支出項目", exp, "總支出", pl.total_expense)

    # 月度淨利趨勢：近 6 個有效月（≤ 本月）
    vms = _valid_months_desc(all_months, lambda m: cf.complete(m, clinic), month)[:6]
    vms = list(reversed(vms))
    labels = [roc_label(m) for m in vms]
    netvals = [cf.clinic_pl(m, clinic).net for m in vms]
    body += "<h2>月度淨利趨勢（近 6 個有效月份）</h2>"
    body += svg_line_chart(labels, [Series(f"{clinic}淨利", netvals, BRAND)])

    title = f"{clinic} 月度實帳金流分析報表"
    subtitle = f"報表月份：{roc_label(month)}　·　實帳模式（N 月帳上發生記 N 月）"
    return _doc(title, subtitle, body, generated), None


# ════════════════════════════════════════════════════════════
# 報表 B：季度實帳金流分析
# ════════════════════════════════════════════════════════════

def build_cashflow_quarterly(sb, clinic: str, year: int, q: int,
                             all_months: list[str], generated: str):
    cf = CashflowData(sb)
    all_set = set(all_months)
    period = [m for m in months_of_quarter(year, q) if m in all_set]
    if not period:
        return None, _incomplete_note(
            [f"{year} Q{q}"], [f"{year} 第 {q} 季尚無任何月份資料"])
    used = [m for m in period if cf.complete(m, clinic)]
    skipped = [roc_label(m) for m in period if not cf.complete(m, clinic)]
    if not used:
        return None, _incomplete_note(
            [f"{year} 第 {q} 季"],
            [iss for m in period for iss in cf.issues(m, clinic)])

    pls = [cf.clinic_pl(m, clinic) for m in used]
    inc = [p.total_income for p in pls]
    exp = [p.total_expense for p in pls]
    net = [p.net for p in pls]
    q_inc, q_exp, q_net = sum(inc), sum(exp), sum(net)

    body = _skipped_note(skipped)
    body += _kpis([
        ("季總收入", q_inc, False),
        ("季總支出", q_exp, False),
        ("季度淨利", q_net, True),
    ])
    body += "<h2>季度各月收入 / 支出 / 淨利</h2>"
    body += _month_breakdown_table([roc_label(m) for m in used], inc, exp, net)

    # 季度淨利趨勢：近 6 個有效季
    vqs = _valid_quarters(all_set, lambda m: cf.complete(m, clinic),
                          (year, q), 6)
    labels = [f"{yy}Q{qq}" for yy, qq in vqs]
    qnet = [sum(cf.clinic_pl(m, clinic).net
                for m in months_of_quarter(yy, qq)) for yy, qq in vqs]
    body += "<h2>季度淨利趨勢（近 6 個有效季，自 2026 起）</h2>"
    body += svg_line_chart(labels, [Series(f"{clinic}季淨利", qnet, BRAND)])

    title = f"{clinic} 季度實帳金流分析報表"
    subtitle = (f"報表季度：{year} 第 {q} 季"
                f"（涵蓋 {roc_label(used[0])} ~ {roc_label(used[-1])}）")
    return _doc(title, subtitle, body, generated), None


# ════════════════════════════════════════════════════════════
# 報表 C：年度實帳金流分析
# ════════════════════════════════════════════════════════════

def build_cashflow_yearly(sb, clinic: str, year: int,
                          all_months: list[str], generated: str):
    cf = CashflowData(sb)
    all_set = set(all_months)
    period = [m for m in months_of_year(year) if m in all_set]
    if not period:
        return None, _incomplete_note(
            [str(year)], [f"{year} 年尚無任何月份資料"])
    used = [m for m in period if cf.complete(m, clinic)]
    skipped = [roc_label(m) for m in period if not cf.complete(m, clinic)]
    if not used:
        return None, _incomplete_note(
            [f"{year} 年"],
            [iss for m in period for iss in cf.issues(m, clinic)])

    pls = [cf.clinic_pl(m, clinic) for m in used]
    inc = [p.total_income for p in pls]
    exp = [p.total_expense for p in pls]
    net = [p.net for p in pls]

    body = _skipped_note(skipped)
    body += _kpis([
        ("年總收入", sum(inc), False),
        ("年總支出", sum(exp), False),
        ("年度淨利", sum(net), True),
    ])
    body += "<h2>年度各月收入 / 支出 / 淨利</h2>"
    body += _month_breakdown_table([roc_label(m) for m in used], inc, exp, net)

    # 年度淨利趨勢：近 3 個有效年
    vys = _valid_years(all_set, lambda m: cf.complete(m, clinic), year, 3)
    labels = [str(yy) for yy in vys]
    ynet = [sum(cf.clinic_pl(m, clinic).net
                for m in months_of_year(yy) if m in all_set) for yy in vys]
    body += "<h2>年度淨利趨勢（近 3 個有效年，自 2026 起）</h2>"
    body += svg_line_chart(labels, [Series(f"{clinic}年淨利", ynet, BRAND)])

    title = f"{clinic} 年度實帳金流分析報表"
    subtitle = (f"報表年度：{year} 年"
                f"（涵蓋 {roc_label(used[0])} ~ {roc_label(used[-1])}）")
    return _doc(title, subtitle, body, generated), None


# ════════════════════════════════════════════════════════════
# 報表 D：月度損益分析
# ════════════════════════════════════════════════════════════

def build_pl_monthly(sb, clinic: str, month: str,
                     all_months: list[str], generated: str):
    pld = PLData(sb)
    if not pld.complete(month, clinic):
        return None, _incomplete_note(
            [roc_label(month)],
            [f"{roc_label(month)} G(總收入) 或 H(薪資合計) 為 0，資料未完全"])

    pl = pld.clinic_pl(month, clinic)
    body = _kpis([
        ("總收入", pl.g_total_income, False),
        ("總支出（含合約）", pl.n_total_expense_a, False),
        ("盈餘", pl.o_profit_a, True),
    ])

    inc_rows = [("健保醫療給付", pl.b_nhi_paid), ("現金總收入", pl.c_cash_revenue)]
    if clinic == "澤豐":
        inc_rows.append(("澤沛金流匯入", pl.e_zepei_inflow))
    inc_rows.append(("其餘收入", pl.f_other_income))
    body += "<h2>收入分類細項</h2>" + _cat_table(
        "收入項目", inc_rows, "總收入", pl.g_total_income)

    exp_rows = [("薪資支出合計", pl.h_salary_total), ("現金支出", pl.i_cash_expense)]
    if clinic == "澤沛":
        exp_rows.append(("澤沛金流支出", pl.j_zepei_outflow))
    exp_rows.append(("合約支出", pl.k_contract))
    if clinic == "澤沛":
        exp_rows.append(("房租支出", pl.l_rent))
    exp_rows.append(("其餘支出", pl.m_other_expense))
    body += "<h2>支出分類細項</h2>" + _cat_table(
        "支出項目", exp_rows, "總支出（含合約）", pl.n_total_expense_a)

    # 3 個趨勢：收入 / 支出 / 盈餘 — 近 6 有效月
    vms = list(reversed(
        _valid_months_desc(all_months, lambda m: pld.complete(m, clinic),
                           month)[:6]))
    labels = [roc_label(m) for m in vms]
    g = [pld.clinic_pl(m, clinic).g_total_income for m in vms]
    nn = [pld.clinic_pl(m, clinic).n_total_expense_a for m in vms]
    o = [pld.clinic_pl(m, clinic).o_profit_a for m in vms]
    body += "<h2>月度收入趨勢（近 6 個有效月份）</h2>"
    body += svg_line_chart(labels, [Series("總收入", g, BLUE)])
    body += "<h2>月度支出趨勢（近 6 個有效月份）</h2>"
    body += svg_line_chart(labels, [Series("總支出", nn, RED)])
    body += "<h2>月度盈餘趨勢（近 6 個有效月份）</h2>"
    body += svg_line_chart(labels, [Series("盈餘", o, GREEN)])

    title = f"{clinic} 月度損益分析報表"
    subtitle = f"報表月份：{roc_label(month)}　·　會計精神（歸屬月份制）"
    return _doc(title, subtitle, body, generated), None


# ════════════════════════════════════════════════════════════
# 報表 E：年度損益分析
# ════════════════════════════════════════════════════════════

def build_pl_yearly(sb, clinic: str, year: int,
                    all_months: list[str], generated: str):
    pld = PLData(sb)
    all_set = set(all_months)
    period = [m for m in months_of_year(year) if m in all_set]
    if not period:
        return None, _incomplete_note(
            [str(year)], [f"{year} 年尚無任何月份資料"])
    used = [m for m in period if pld.complete(m, clinic)]
    skipped = [roc_label(m) for m in period if not pld.complete(m, clinic)]
    if not used:
        return None, _incomplete_note(
            [f"{year} 年"],
            [f"{roc_label(m)} 總收入或薪資為 0，資料未完全" for m in period])

    pls = [pld.clinic_pl(m, clinic) for m in used]
    g = sum(p.g_total_income for p in pls)
    n = sum(p.n_total_expense_a for p in pls)
    o = sum(p.o_profit_a for p in pls)

    body = _skipped_note(skipped)
    body += _kpis([
        ("年總收入", g, False),
        ("年總支出", n, False),
        ("年總盈餘", o, True),
    ])
    body += "<h2>年度各月 收入 / 支出 / 盈餘</h2>"
    body += _month_breakdown_table(
        [roc_label(m) for m in used],
        [p.g_total_income for p in pls],
        [p.n_total_expense_a for p in pls],
        [p.o_profit_a for p in pls],
        net_label="盈餘")

    vys = _valid_years(all_set, lambda m: pld.complete(m, clinic), year, 3)
    labels = [str(yy) for yy in vys]
    yo = [sum(pld.clinic_pl(m, clinic).o_profit_a
              for m in months_of_year(yy) if m in all_set) for yy in vys]
    body += "<h2>年總盈餘趨勢（近 3 個有效年，自 2026 起）</h2>"
    body += svg_line_chart(labels, [Series(f"{clinic}年盈餘", yo, GREEN)])

    title = f"{clinic} 年度損益分析報表"
    subtitle = (f"報表年度：{year} 年"
                f"（涵蓋 {roc_label(used[0])} ~ {roc_label(used[-1])}）")
    return _doc(title, subtitle, body, generated), None


# ════════════════════════════════════════════════════════════
# 不完整註記
# ════════════════════════════════════════════════════════════

def _skipped_note(skipped: list[str]) -> str:
    """期間內已跳過的不完整月份提示（報表仍照常產出其餘月份）。"""
    if not skipped:
        return ""
    return (
        "<div class='note'>ℹ️ 已跳過資料未完整的月份（不納入本報表）："
        f"{'、'.join(skipped)}。</div>"
    )


def _incomplete_note(periods: list[str], issues: list[str]) -> str:
    plist = "、".join(periods)
    uniq: list[str] = []
    for i in issues:
        if i not in uniq:
            uniq.append(i)
    detail = "".join(f"<li>{_html.escape(i)}</li>" for i in uniq[:20])
    return (
        f"⚠️ {plist} 資料未完全，無法形成報表。"
        + (f"<ul>{detail}</ul>" if detail else "")
    )


# ════════════════════════════════════════════════════════════
# 澤沛合夥人月度財務報表（給胡舒婷醫師）
#   內容同「月度實帳金流分析」澤沛區塊：收入/支出細項 + 逐筆明細
#   + 月度淨利趨勢；不含支票支出（獨立項目本就不屬澤沛實帳）。
#   視覺獨立美化（卡片式、漸層抬頭），與 A~E 報表分開的 CSS。
# ════════════════════════════════════════════════════════════

_PARTNER_SETTLE_LABEL = {
    "x5_cash": "現金結算（→周院長）",
    "x6_fengpei": "豐沛金流（→澤豐）",
    "x7_contract": "合約結算（→周院長）",
}

_PARTNER_CSS = """
* { box-sizing:border-box; margin:0; padding:0;
    -webkit-print-color-adjust:exact !important;
    print-color-adjust:exact !important; }
@page { size:A4 portrait; margin:10mm; }
body { font-family:"Microsoft JhengHei","PingFang TC","Noto Sans TC",sans-serif;
  background:#f4f4f9; color:#2b2b3a; font-size:13px; padding:24px 12px; }
.sheet { max-width:820px; margin:0 auto; background:#fff; border-radius:18px;
  box-shadow:0 6px 28px rgba(80,70,160,.12); overflow:hidden; }
.hero { background:linear-gradient(120deg,#6A5ACD 0%,#8E7CE8 55%,#FFA07A 130%);
  color:#fff; padding:26px 32px 22px; }
.hero h1 { font-size:23px; letter-spacing:1px; font-weight:800; }
.hero .sub { margin-top:6px; font-size:13px; opacity:.92; }
.hero .tag { display:inline-block; margin-top:10px; background:rgba(255,255,255,.18);
  border:1px solid rgba(255,255,255,.45); border-radius:99px; padding:3px 14px;
  font-size:11.5px; letter-spacing:2px; }
.wrap { padding:22px 32px 26px; }
.kpis { display:flex; gap:14px; margin-bottom:20px; }
.kpi { flex:1; border-radius:14px; padding:14px 16px; }
.kpi .label { font-size:11.5px; letter-spacing:1px; opacity:.75; }
.kpi .value { font-size:21px; font-weight:800; margin-top:4px;
  font-variant-numeric:tabular-nums; }
.kpi.inc { background:#eef4ff; color:#1d4ed8; }
.kpi.exp { background:#fff1ec; color:#c2410c; }
.kpi.net-pos { background:#ecfdf3; color:#15803d; }
.kpi.net-neg { background:#fef2f2; color:#b91c1c; }
h2 { font-size:15.5px; color:#4c3fb0; margin:22px 0 10px; display:flex;
  align-items:center; gap:8px; }
h2::before { content:""; width:5px; height:18px; border-radius:3px;
  background:linear-gradient(#6A5ACD,#FFA07A); }
h3 { font-size:12.5px; color:#7a76a8; margin:14px 0 6px; font-weight:600; }
table { width:100%; border-collapse:collapse; font-size:12.3px; }
th { text-align:left; color:#6b6790; font-weight:600; font-size:11.5px;
  letter-spacing:.5px; padding:7px 10px; border-bottom:2px solid #e8e6f7; }
td { padding:6px 10px; border-bottom:1px solid #f0eff8; }
tbody tr:nth-child(even) td { background:#fbfaff; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
tr.total td { font-weight:800; background:#f3f1fb !important; color:#4c3fb0;
  border-top:2px solid #d9d4f2; border-bottom:none; }
tr.sub td { color:#948fbd; font-size:11.3px; }
tr.sub td:first-child { padding-left:26px; }
.hintline { color:#a5a1c4; font-size:10.6px; margin:4px 0 2px; }
.subnote { color:#a5a1c4; font-size:10.6px; margin:4px 0 8px; }
.trend-card { background:#fbfaff; border:1px solid #eceafb; border-radius:14px;
  padding:14px 14px 6px; margin-top:6px; }
.trend-svg { width:100%; height:auto; }
.svg-title { font-size:13px; font-weight:700; fill:#4c3fb0; }
.axis { font-size:10px; fill:#999; } .pt { font-size:9.5px; fill:#555; }
.legend { font-size:10px; fill:#666; }
.grid { stroke:#eee; stroke-width:1; } .zero { stroke:#ccc; stroke-width:1.2; }
.chart-empty { color:#999; font-size:12px; padding:18px; text-align:center; }
.note { background:#fff9ec; border:1px solid #ffe3a3; border-radius:10px;
  padding:10px 14px; color:#8a5a00; font-size:12px; margin:10px 0; }
.foot { margin-top:22px; padding-top:10px; border-top:1px dashed #e3e0f3;
  color:#a5a1c4; font-size:10.5px; display:flex; justify-content:space-between; }
@media print {
  body { background:#fff; padding:0; }
  .sheet { box-shadow:none; border-radius:0; max-width:none; }
}
"""


def _partner_tx_table(rows: list[dict], *, date_key: str,
                      desc_keys: list[str], show_settle: bool = False) -> str:
    """逐筆明細表：日期 / 項目說明(摘要+備註) / (分類) / 金額。"""
    if not rows:
        return "<div class='hintline'>（本月無資料）</div>"
    cells = []
    for r in rows:
        desc = "　".join(
            str(r.get(k) or "").strip() for k in desc_keys
            if str(r.get(k) or "").strip()
        ) or "—"
        settle_td = ""
        if show_settle:
            lab = _PARTNER_SETTLE_LABEL.get(r.get("settle_kind") or "", "")
            settle_td = f"<td>{_html.escape(lab)}</td>"
        cells.append(
            f"<tr><td>{_html.escape(str(r.get(date_key) or ''))}</td>"
            f"<td>{_html.escape(desc)}</td>{settle_td}"
            f"<td class='num'>{_fmt(r.get('amount'))}</td></tr>"
        )
    total = sum(int(r.get("amount") or 0) for r in rows)
    span = 3 if show_settle else 2
    head = ("<tr><th style='width:92px'>日期</th><th>項目說明</th>"
            + ("<th style='width:150px'>分類</th>" if show_settle else "")
            + "<th class='num' style='width:110px'>金額 (NT$)</th></tr>")
    return (
        f"<table><thead>{head}</thead><tbody>{''.join(cells)}"
        f"<tr class='total'><td colspan='{span}'>小計（{len(rows)} 筆）</td>"
        f"<td class='num'>{_fmt(total)}</td></tr></tbody></table>"
    )


def _partner_doc(title: str, subtitle: str, body: str, generated: str) -> str:
    """合夥人報表共用 HTML 骨架（漸層 hero + 卡片）。"""
    return (
        "<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>"
        f"<title>{_html.escape(title)}</title>"
        f"<style>{_PARTNER_CSS}</style></head><body><div class='sheet'>"
        f"<div class='hero'><h1>{_html.escape(title)}</h1>"
        f"<div class='sub'>{_html.escape(subtitle)}</div>"
        "<div class='tag'>合 夥 人 報 表</div></div>"
        f"<div class='wrap'>{body}"
        "<div class='foot'><span>澤豐聯盟財務系統</span>"
        f"<span>產生時間 {generated}　·　瀏覽器 Ctrl+P 可列印</span></div>"
        "</div></div></body></html>"
    )


def _partner_kpis(items: list[tuple[str, int, bool]]) -> str:
    """items: (label, value, is_profit)。is_profit → 正綠負紅。"""
    cells = []
    for label, value, is_profit in items:
        if is_profit:
            cls = "net-pos" if value >= 0 else "net-neg"
        else:
            cls = "inc" if "收入" in label else "exp"
        cells.append(
            f"<div class='kpi {cls}'><div class='label'>{_html.escape(label)}"
            f"</div><div class='value'>NT$ {_fmt(value)}</div></div>"
        )
    return f"<div class='kpis'>{''.join(cells)}</div>"


def build_zepei_partner_monthly(sb, month: str, all_months: list[str],
                                generated: str):
    """澤沛合夥人月度財務報表 → (html, err)。err 非 None 表示資料未完整。"""
    clinic = "澤沛"
    cf = CashflowData(sb)
    issues = cf.issues(month, clinic)
    if issues:
        return None, _incomplete_note([roc_label(month)], issues)

    pl = cf.clinic_pl(month, clinic)

    kpis = _partner_kpis([
        ("總收入", pl.total_income, False),
        ("總支出", pl.total_expense, False),
        ("本月淨利", pl.net, True),
    ])

    # 收入 / 支出分類小計（同月度實帳金流分析）
    inc_rows = cashflow_income_rows(clinic, pl)
    exp_rows = cashflow_expense_rows(clinic, pl)
    body = kpis
    body += "<h2>收入細項</h2>"
    body += _cat_table("收入項目", inc_rows, "總收入", pl.total_income)
    body += "<h3>玉山健保戶入帳明細</h3>"
    body += _partner_tx_table(pl.esun_inflow_items, date_key="transaction_date",
                              desc_keys=["summary", "note"])
    body += "<h3>中信進出戶入帳明細</h3>"
    body += _partner_tx_table(pl.ctbc_inflow_items, date_key="transaction_date",
                              desc_keys=["summary", "note"])
    if pl.x10_income_items:
        body += "<h3>其他收入（手動補登）</h3>"
        body += _partner_tx_table(pl.x10_income_items, date_key="entry_date",
                                  desc_keys=["description"])

    body += "<h2>支出細項</h2>"
    body += _cat_table("支出項目", exp_rows, "總支出", pl.total_expense)
    body += "<h3>玉山健保戶出帳明細</h3>"
    body += _partner_tx_table(pl.esun_outflow_items, date_key="transaction_date",
                              desc_keys=["summary", "note"])
    body += "<h3>中信進出戶出帳明細（含結算分類）</h3>"
    body += _partner_tx_table(pl.ctbc_outflow_items, date_key="transaction_date",
                              desc_keys=["summary", "note"], show_settle=True)
    # 前月現金結算細項 — 合夥人重點對帳區，固定顯示
    if pl.x5_detail_items or pl.cash_settle_outflow:
        body += ("<h3>現金結算細項（前月現金支出逐筆；金額已含於中信"
                 "「現金結算」、不重複加總）</h3>")
        if pl.x5_detail_items:
            body += _partner_tx_table(
                pl.x5_detail_items, date_key="expense_date",
                desc_keys=["description"],
            )
            if (pl.cash_settle_outflow
                    and pl.x5_detail_total != pl.cash_settle_outflow):
                body += (
                    "<div class='note'>⚠️ 現金細項合計 "
                    f"{_fmt(pl.x5_detail_total)} 與本月現金結算轉帳 "
                    f"{_fmt(pl.cash_settle_outflow)} 不同"
                    f"（差 {_fmt(pl.cash_settle_outflow - pl.x5_detail_total)}）"
                    "。</div>"
                )
        else:
            body += (
                "<div class='note'>ℹ️ 本月現金結算 NT$ "
                f"{_fmt(pl.cash_settle_outflow)} 的逐筆細項尚未上傳"
                "（澤沛現金支出檔），僅顯示結算總額。</div>"
            )
    if pl.x10_expense_items:
        body += "<h3>其他支出（手動補登）</h3>"
        body += _partner_tx_table(pl.x10_expense_items, date_key="entry_date",
                                  desc_keys=["description"])

    # 月度淨利趨勢（近 12 個有效月，澤沛）
    vms = _valid_months_desc(
        all_months, lambda m: cf.complete(m, clinic), month)[:12]
    vms = list(reversed(vms))
    labels = [roc_label(m) for m in vms]
    netvals = [cf.clinic_pl(m, clinic).net for m in vms]
    body += "<h2>月度淨利趨勢</h2><div class='trend-card'>"
    body += svg_line_chart(labels, [Series("澤沛淨利", netvals, BRAND)],
                           width=740, height=260)
    body += "</div>"

    return _partner_doc(
        "澤沛中醫診所 · 月度財務報表",
        f"報表月份：{roc_label(month)}　·　"
        "實帳模式（當月帳上實際發生的款項記在當月）",
        body, generated,
    ), None


def build_zepei_partner_quarterly(sb, year: int, q: int,
                                  all_months: list[str], generated: str):
    """澤沛合夥人季度財務報表 → (html, err)。

    內容：本季 3 個月收入/支出/淨利表格 + 近 12 個有效月淨利趨勢
          + 近 6 個有效季淨利趨勢（皆澤沛）。
    完整度：不完整月份跳過並註記；全季皆不完整 → err。
    """
    clinic = "澤沛"
    cf = CashflowData(sb)
    all_set = set(all_months)
    period = [m for m in months_of_quarter(year, q) if m in all_set]
    if not period:
        return None, _incomplete_note(
            [f"{year} Q{q}"], [f"{year} 第 {q} 季尚無任何月份資料"])
    used = [m for m in period if cf.complete(m, clinic)]
    skipped = [roc_label(m) for m in period if not cf.complete(m, clinic)]
    if not used:
        return None, _incomplete_note(
            [f"{year} 第 {q} 季"],
            [iss for m in period for iss in cf.issues(m, clinic)])

    pls = [cf.clinic_pl(m, clinic) for m in used]
    inc = [p.total_income for p in pls]
    exp = [p.total_expense for p in pls]
    net = [p.net for p in pls]

    body = ""
    if skipped:
        body += ("<div class='note'>ℹ️ 已跳過資料未完整的月份（不納入本報表）："
                 f"{'、'.join(skipped)}。</div>")
    body += _partner_kpis([
        ("季總收入", sum(inc), False),
        ("季總支出", sum(exp), False),
        ("季度淨利", sum(net), True),
    ])
    body += "<h2>本季各月收入／支出／淨利</h2>"
    body += _month_breakdown_table([roc_label(m) for m in used], inc, exp, net)

    # 月度淨利趨勢：近 12 個有效月（以本季最後一個有效月為終點）
    vms = _valid_months_desc(
        all_months, lambda m: cf.complete(m, clinic), used[-1])[:12]
    vms = list(reversed(vms))
    body += "<h2>月度淨利趨勢（近 12 個有效月）</h2><div class='trend-card'>"
    body += svg_line_chart(
        [roc_label(m) for m in vms],
        [Series("澤沛月淨利", [cf.clinic_pl(m, clinic).net for m in vms],
                BRAND)],
        width=740, height=260,
    )
    body += "</div>"

    # 季度淨利趨勢：近 6 個有效季（3 個月皆存在且完整）
    vqs = _valid_quarters(all_set, lambda m: cf.complete(m, clinic),
                          (year, q), 6)
    body += "<h2>季度淨利趨勢（近 6 個有效季）</h2><div class='trend-card'>"
    body += svg_line_chart(
        [f"{yy}Q{qq}" for yy, qq in vqs],
        [Series("澤沛季淨利",
                [sum(cf.clinic_pl(m, clinic).net
                     for m in months_of_quarter(yy, qq)) for yy, qq in vqs],
                BRAND)],
        width=740, height=260,
    )
    body += "</div>"

    return _partner_doc(
        "澤沛中醫診所 · 季度財務報表",
        f"報表季度：{year} 第 {q} 季"
        f"（涵蓋 {roc_label(used[0])} ~ {roc_label(used[-1])}）　·　實帳模式",
        body, generated,
    ), None
