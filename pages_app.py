"""
功能頁面集合
每個 page_xxx() 對應 sidebar 一個選單項
"""

import streamlit as st
import pandas as pd
from db import get_authed_client


# 全系統服務月份下限（民國 115 年 1 月 = 2026-01）：114-12 及更早資料不完整，不顯示
MIN_SERVICE_MONTH = "2026-01-01"


def _filter_min_month(months):
    """過濾掉早於 MIN_SERVICE_MONTH 的月份。接受 list[str] / set[str] 等可疊代物件。"""
    return [m for m in months if m and m >= MIN_SERVICE_MONTH]


# ─── 計算結果快取（提速；資料指紋變動時自動失效）──────────────
def _data_version(sb) -> tuple:
    """各上游表筆數組成的輕量指紋；任一表新增/刪除即改變 → 快取自動失效。
    不快取本函式（須反映當下狀態）；count 查詢便宜。

    manual_annotation 另做**內容雜湊**：金流備註常被就地修改（UPDATE
    金額/分類/日期，筆數不變），只算筆數會讓修改後 30 分鐘內看到舊結果
    （2026-09-02 院長私人帳務改金額不生效的根因）。表小（數十筆），
    全表雜湊便宜。"""
    def c(t):
        try:
            return (sb.table(t).select("id", count="exact")
                    .limit(1).execute().count or 0)
        except Exception:
            return 0

    def ann_hash():
        import hashlib
        import json
        cols = ("id, entry_date, amount, category, account, form, "
                "clinic_id, description, gross_amount, cash_salary_deduction")
        try:
            rows = sb.table("manual_annotation").select(cols).execute().data
        except Exception:
            try:  # v13 migration 未跑
                rows = sb.table("manual_annotation").select(
                    "id, entry_date, amount, category, account, form, "
                    "clinic_id, description"
                ).execute().data
            except Exception:
                return "na"
        blob = json.dumps(rows, sort_keys=True, ensure_ascii=False,
                          default=str).encode("utf-8")
        return hashlib.md5(blob).hexdigest()
    return tuple(c(t) for t in (
        "bank_transactions", "nhi_payment_notices",
        "manual_entry", "doctor_salary_monthly", "staff_salary_summary",
        "contract_expense", "cash_expense", "check_expense",
    )) + (ann_hash(),)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_both_pl(_sb, service_month: str, version: tuple):
    from data_processor.monthly_profit_loss import calculate_both_pl
    return calculate_both_pl(_sb, service_month)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_pl_health(_sb, service_month: str, version: tuple):
    from data_processor.data_health import compute_pl_health
    return compute_pl_health(_sb, service_month)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_both_clinics(_sb, service_month: str, version: tuple):
    from data_processor.monthly_pl import calculate_both_clinics
    return calculate_both_clinics(_sb, service_month)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_check_month(_sb, service_month: str, version: tuple):
    from data_processor.monthly_pl import calculate_check_expense_month
    return calculate_check_expense_month(_sb, service_month)


# ============================================================
# 1. 業績儀表板（Phase 3）
# ============================================================
def page_dashboard():
    st.title("📊 業績與財務儀表板")

    import altair as alt

    sb = get_authed_client()

    # ─── 載入資料 ───
    try:
        clinics_data = sb.table("clinics").select("id, short_name").execute().data
        doctors_data = sb.table("doctors").select("id, name").execute().data
        outpatient = sb.table("doctor_outpatient_summary").select("*").execute().data
        cash_monthly = sb.table("doctor_cash_monthly").select("*").execute().data
        visit_stats = sb.table("doctor_visit_stats").select("*").execute().data
        clinic_rates = sb.table("clinic_visit_rates").select("*").execute().data
    except Exception as e:
        st.error(f"資料庫讀取失敗：{e}")
        return

    cid_to_short = {c["id"]: c["short_name"] for c in clinics_data}
    did_to_name = {d["id"]: d["name"] for d in doctors_data}

    if not (outpatient or cash_monthly or visit_stats):
        st.warning("⚠️ 尚無業績資料，請先到「本月資料匯入」上傳健保人數+初診、門診申報金額、自費統計。")
        return

    # ─── 篩選 ───
    out_df = pd.DataFrame(outpatient) if outpatient else pd.DataFrame()
    cash_df = pd.DataFrame(cash_monthly) if cash_monthly else pd.DataFrame()
    visit_df = pd.DataFrame(visit_stats) if visit_stats else pd.DataFrame()

    all_months = sorted(_filter_min_month(set(
        list(out_df["service_month"].unique() if not out_df.empty else [])
        + list(cash_df["service_month"].unique() if not cash_df.empty else [])
        + list(visit_df["service_month"].unique() if not visit_df.empty else [])
    )), reverse=True)
    if not all_months:
        st.warning("⚠️ 尚無資料")
        return

    col_f1, col_f2 = st.columns([2, 3])
    with col_f1:
        clinic_filter = st.radio(
            "診所", ["全部", "澤豐", "澤沛"],
            horizontal=True, key="dash_clinic",
        )
    with col_f2:
        sel_months = st.multiselect(
            "月份（可多選）",
            options=all_months,
            default=all_months[:3],
            format_func=lambda d: d[:7],
            key="dash_months",
        )

    if not sel_months:
        st.info("請選至少一個月份")
        return

    def filter_df(df):
        if df.empty:
            return df
        out = df[df["service_month"].isin(sel_months)].copy()
        if clinic_filter != "全部":
            cid = next(c["id"] for c in clinics_data if c["short_name"] == clinic_filter)
            out = out[out["clinic_id"] == cid]
        return out

    out_f = filter_df(out_df)
    cash_f = filter_df(cash_df)
    visit_f = filter_df(visit_df)

    # 加 clinic_name + doctor_name 欄
    for df in (out_f, cash_f, visit_f):
        if df.empty:
            continue
        df["診所"] = df["clinic_id"].map(cid_to_short)
        df["醫師"] = df["doctor_id"].map(did_to_name)
        df["月份"] = df["service_month"].str[:7]

    # ─── KPI 卡片 ───
    st.divider()
    nhi_total = int(out_f["nhi_total_points"].sum()) if not out_f.empty else 0
    cash_total = int(cash_f["cash_total_excl_reg"].sum()) if not cash_f.empty else 0
    visit_total = int(visit_f["nhi_visits_total"].sum()) if not visit_f.empty else 0
    sessions_total = int(visit_f["sessions_total"].sum()) if not visit_f.empty else 0
    avg_visits = round(visit_total / sessions_total, 2) if sessions_total else 0

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("健保申報合計", f"${nhi_total:,}")
    k2.metric("自費合計（不含掛號）", f"${cash_total:,}")
    k3.metric("總業績", f"${nhi_total + cash_total:,}")
    k4.metric("健保看診人次", f"{visit_total:,}")
    k5.metric("平均人次/診", f"{avg_visits}")

    # ─── 圓餅：醫師業績佔比 ───
    st.divider()
    st.subheader("🥧 醫師業績佔比（健保 + 自費）")

    if out_f.empty and cash_f.empty:
        st.info("該篩選條件下無資料")
    else:
        # 用 (診所, 醫師) 作為 group key，因為跨支援會有兩條記錄
        nhi_by = (
            out_f.groupby(["診所", "醫師"])["nhi_total_points"].sum().reset_index()
            if not out_f.empty else pd.DataFrame(columns=["診所", "醫師", "nhi_total_points"])
        )
        cash_by = (
            cash_f.groupby(["診所", "醫師"])["cash_total_excl_reg"].sum().reset_index()
            if not cash_f.empty else pd.DataFrame(columns=["診所", "醫師", "cash_total_excl_reg"])
        )
        merged = nhi_by.merge(cash_by, on=["診所", "醫師"], how="outer").fillna(0)
        merged["業績合計"] = merged["nhi_total_points"] + merged["cash_total_excl_reg"]
        merged["醫師(診所)"] = merged["醫師"] + "(" + merged["診所"] + ")"
        merged = merged[merged["業績合計"] > 0]

        if not merged.empty:
            c_pie1, c_pie2 = st.columns(2)
            with c_pie1:
                pie = alt.Chart(merged).mark_arc(innerRadius=50).encode(
                    theta=alt.Theta("業績合計:Q"),
                    color=alt.Color("醫師(診所):N", legend=alt.Legend(title="醫師(診所)")),
                    tooltip=["醫師(診所)", alt.Tooltip("業績合計:Q", format=",")],
                ).properties(height=350, title="總業績佔比")
                st.altair_chart(pie, use_container_width=True)
            with c_pie2:
                # 健保 vs 自費 stacked bar by doctor
                long = merged.melt(
                    id_vars=["醫師(診所)"],
                    value_vars=["nhi_total_points", "cash_total_excl_reg"],
                    var_name="類別", value_name="金額",
                )
                long["類別"] = long["類別"].map({
                    "nhi_total_points": "健保",
                    "cash_total_excl_reg": "自費",
                })
                bar = alt.Chart(long).mark_bar().encode(
                    x=alt.X("醫師(診所):N", sort="-y"),
                    y=alt.Y("金額:Q"),
                    color=alt.Color(
                        "類別:N",
                        scale=alt.Scale(range=["#6A5ACD", "#FFA07A"]),
                    ),
                    tooltip=["醫師(診所)", "類別", alt.Tooltip("金額:Q", format=",")],
                ).properties(height=350, title="健保 vs 自費（分醫師）")
                st.altair_chart(bar, use_container_width=True)

    # ─── 月度趨勢柱狀圖 ───
    st.divider()
    st.subheader("📅 月度業績趨勢（堆疊：健保 + 自費）")

    nhi_by_m = (
        out_f.groupby(["月份", "診所"])["nhi_total_points"].sum().reset_index()
        if not out_f.empty else pd.DataFrame(columns=["月份", "診所", "nhi_total_points"])
    )
    cash_by_m = (
        cash_f.groupby(["月份", "診所"])["cash_total_excl_reg"].sum().reset_index()
        if not cash_f.empty else pd.DataFrame(columns=["月份", "診所", "cash_total_excl_reg"])
    )
    merged_m = nhi_by_m.merge(cash_by_m, on=["月份", "診所"], how="outer").fillna(0)
    if not merged_m.empty:
        long_m = merged_m.melt(
            id_vars=["月份", "診所"],
            value_vars=["nhi_total_points", "cash_total_excl_reg"],
            var_name="類別", value_name="金額",
        )
        long_m["類別"] = long_m["類別"].map({
            "nhi_total_points": "健保", "cash_total_excl_reg": "自費",
        })
        bar2 = alt.Chart(long_m).mark_bar().encode(
            x=alt.X("月份:N", sort="ascending"),
            y=alt.Y("金額:Q", stack="zero"),
            color=alt.Color("類別:N", scale=alt.Scale(range=["#6A5ACD", "#FFA07A"])),
            xOffset="診所:N",
            tooltip=["月份", "診所", "類別", alt.Tooltip("金額:Q", format=",")],
        ).properties(height=350)
        st.altair_chart(bar2, use_container_width=True)

    # ─── 初診人數 & 初診率分析（每診所一張小圖，左柱人數、右柱率；雙 Y 軸）───
    st.divider()
    st.subheader("🆕 初診人數 & 初診率分析")
    st.caption("每月一組雙柱：左柱=初診人數（左 Y 軸），右柱=初診率(%)（右 Y 軸）。")
    rates_df = pd.DataFrame(clinic_rates) if clinic_rates else pd.DataFrame()
    if rates_df.empty:
        st.info("尚無 clinic_visit_rates 資料（健保人數+初診統計表）")
    else:
        rates_f = rates_df[rates_df["service_month"].isin(sel_months)].copy()
        if clinic_filter != "全部":
            cid = next(c["id"] for c in clinics_data if c["short_name"] == clinic_filter)
            rates_f = rates_f[rates_f["clinic_id"] == cid]
        if rates_f.empty:
            st.info("該篩選條件下無初診資料")
        else:
            rates_f["診所"] = rates_f["clinic_id"].map(cid_to_short)
            rates_f["月份"] = rates_f["service_month"].str[:7]
            rates_f["初診率(%)"] = (
                pd.to_numeric(rates_f["first_visit_rate"], errors="coerce") * 100
            ).round(2)
            rates_f["初診人數"] = pd.to_numeric(
                rates_f["first_visit_count"], errors="coerce"
            ).fillna(0).astype(int)

            # 左半=澤豐、右半=澤沛 各一張獨立 chart（用 st.columns 分欄；hconcat 在
            # use_container_width=True 下無法均分，會把每張圖壓到 150px 左右）
            cols = st.columns(2)
            for i, c_short in enumerate(["澤豐", "澤沛"]):
                with cols[i]:
                    sub = rates_f[rates_f["診所"] == c_short].sort_values("月份")
                    if sub.empty:
                        st.info(f"{c_short}：該期間無初診資料")
                        continue
                    bar_count = alt.Chart(sub).mark_bar(
                        xOffset=-18, width=32, color="#6A5ACD",
                    ).encode(
                        x=alt.X("月份:N", sort="ascending", title=None),
                        y=alt.Y("初診人數:Q",
                                axis=alt.Axis(title="初診人數", titleColor="#6A5ACD")),
                        tooltip=["月份", alt.Tooltip("初診人數:Q", format=",")],
                    )
                    bar_rate = alt.Chart(sub).mark_bar(
                        xOffset=18, width=32, color="#FFA07A",
                    ).encode(
                        x=alt.X("月份:N", sort="ascending", title=None),
                        y=alt.Y("初診率(%):Q",
                                axis=alt.Axis(title="初診率(%)",
                                              titleColor="#FFA07A", orient="right")),
                        tooltip=["月份", alt.Tooltip("初診率(%):Q", format=".2f")],
                    )
                    sub_chart = alt.layer(bar_count, bar_rate).resolve_scale(
                        y="independent"
                    ).properties(title=c_short, height=340)
                    st.altair_chart(sub_chart, use_container_width=True)

    # ─── 看診結構（健保人次分布）───
    st.divider()
    st.subheader("👥 健保看診結構（人次分布）")

    if not visit_f.empty:
        cat_cols = {
            "內科": "nhi_internal", "純針": "nhi_pure_acu", "純傷": "nhi_pure_trauma",
            "內+針": "nhi_internal_acu", "內+傷": "nhi_internal_trauma",
        }
        agg_cols = {label: visit_f[col].sum() for label, col in cat_cols.items()}
        cat_df = pd.DataFrame([
            {"類別": k, "人次": int(v)} for k, v in agg_cols.items() if v > 0
        ])
        if not cat_df.empty:
            c_v1, c_v2 = st.columns([1, 2])
            with c_v1:
                pie3 = alt.Chart(cat_df).mark_arc(innerRadius=40).encode(
                    theta="人次:Q",
                    color="類別:N",
                    tooltip=["類別", alt.Tooltip("人次:Q", format=",")],
                ).properties(height=300, title="人次類別佔比")
                st.altair_chart(pie3, use_container_width=True)
            with c_v2:
                # 各醫師健保人次堆疊
                doc_cat = visit_f[["診所", "醫師"] + list(cat_cols.values())].copy()
                doc_cat["醫師(診所)"] = doc_cat["醫師"] + "(" + doc_cat["診所"] + ")"
                doc_long = doc_cat.melt(
                    id_vars=["醫師(診所)"],
                    value_vars=list(cat_cols.values()),
                    var_name="類別", value_name="人次",
                )
                col_to_label = {v: k for k, v in cat_cols.items()}
                doc_long["類別"] = doc_long["類別"].map(col_to_label)
                doc_long = doc_long[doc_long["人次"] > 0]
                bar3 = alt.Chart(doc_long).mark_bar().encode(
                    x=alt.X("醫師(診所):N", sort="-y"),
                    y=alt.Y("人次:Q"),
                    color="類別:N",
                    tooltip=["醫師(診所)", "類別", "人次"],
                ).properties(height=300, title="醫師健保人次（堆疊）")
                st.altair_chart(bar3, use_container_width=True)

    # ─── 醫師月份明細表 ───
    st.divider()
    st.subheader("📋 醫師月份明細")

    if not out_f.empty:
        detail = out_f[[
            "月份", "診所", "醫師",
            "nhi_consult_fee", "nhi_drug_fee", "nhi_treatment_fee",
            "nhi_lab_fee", "nhi_total_points",
            "cash_internal", "cash_acupuncture", "registration_fee",
            "acu_complex_mid_count", "acu_complex_high_count", "a91_count",
        ]].rename(columns={
            "nhi_consult_fee": "診察費", "nhi_drug_fee": "內科/藥費",
            "nhi_treatment_fee": "處置費", "nhi_lab_fee": "檢驗費",
            "nhi_total_points": "健保合計",
            "cash_internal": "自費內科", "cash_acupuncture": "自費針傷脫",
            "registration_fee": "掛號費",
            "acu_complex_mid_count": "中複針", "acu_complex_high_count": "高複針",
            "a91_count": "A91",
        })
        detail = detail.sort_values(["月份", "診所", "醫師"])
        st.dataframe(detail, use_container_width=True, hide_index=True)

    # ─── 醫師個人業績比較（下半部，獨立月份選擇器 + 兩院強制顯示）───
    _section_doctor_personal_compare(sb)


# ─── 醫師業績比較區塊（dashboard 下半部）────────────────────
def _nurse_head_count(avg_visits: float) -> int:
    """依當月該醫師健保平均人次判定護理師&助理人數。
    ≤ 10 → 1；10 < x ≤ 15 → 2；15 < x ≤ 30 → 3；> 30 → 4
    """
    if avg_visits <= 10:
        return 1
    if avg_visits <= 15:
        return 2
    if avg_visits <= 30:
        return 3
    return 4


def _get_nurse_cost_params(sb) -> tuple[float, float]:
    """讀 system_settings 取護理師&助理平均薪資、月上班診數；表/列不存在用預設 35000/40。"""
    try:
        rows = (
            sb.table("system_settings")
            .select("key, value")
            .in_("key", ["nurse_monthly_salary", "nurse_monthly_sessions"])
            .execute().data
        )
        d = {r["key"]: float(r["value"]) for r in rows}
        return (
            d.get("nurse_monthly_salary", 35000.0),
            d.get("nurse_monthly_sessions", 40.0),
        )
    except Exception:
        return 35000.0, 40.0


def _productivity_breakdown(
    out_row: dict | None, cash_row: dict | None, clinic_short: str
) -> dict:
    """醫師產值估算公式拆解（依處置費有無拆分選公式）。回傳所有中間值。

    澤豐 ≤11506（48 欄舊制，處置費拆 combo/pure）：
         (診察費 + 內科費*0.2 + 處(內+xx)*0.3 + 純xx*0.5 + 調劑費)*0.9
         + 掛號費 + 自費(內服+外用+保養+飲片)*0.3 + 自費(針+傷+脫)*0.4
         + 自費(檢驗)*0.8 + 自費(診察) + 自費(其他)
    澤沛全期間、澤豐 11507 起（16 欄制，單一處置費）：
         (診察費 + 藥費*0.2 + 處置費*0.5 + 調劑費)*0.9 + 同上自費部分
    """
    g = lambda d, k: (d.get(k) or 0) if d else 0
    consult_nhi = g(out_row, "nhi_consult_fee")
    drug_nhi = g(out_row, "nhi_drug_fee")
    dispense_nhi = g(out_row, "nhi_dispense_fee")
    combo = g(out_row, "nhi_combo_treatment") if clinic_short == "澤豐" else 0
    pure = g(out_row, "nhi_pure_treatment") if clinic_short == "澤豐" else 0
    if combo or pure:  # 澤豐 48 欄舊制（≤11506）才有拆分
        treatment_single = 0
        nhi_pre = (consult_nhi + drug_nhi * 0.2
                   + combo * 0.3 + pure * 0.5 + dispense_nhi)
    else:  # 澤沛全期間 + 澤豐 11507 起：單一處置費 *0.5
        treatment_single = g(out_row, "nhi_treatment_fee")
        nhi_pre = (consult_nhi + drug_nhi * 0.2
                   + treatment_single * 0.5 + dispense_nhi)
    nhi_part = nhi_pre * 0.9
    reg = g(out_row, "registration_fee")
    drug_sum = (
        g(cash_row, "internal_drug") + g(cash_row, "external_drug")
        + g(cash_row, "wellness") + g(cash_row, "herb_decoction")
    )
    acu_sum = (
        g(cash_row, "acupuncture") + g(cash_row, "trauma") + g(cash_row, "dislocation")
    )
    cash_lab = g(cash_row, "lab")
    cash_consult = g(cash_row, "consult")
    cash_other = g(cash_row, "other")
    cash_part = (
        drug_sum * 0.3 + acu_sum * 0.4
        + cash_lab * 0.8 + cash_consult + cash_other
    )
    return {
        "診察費": consult_nhi,
        "內科費(藥費)": drug_nhi,
        "處(內+xx)": combo,        # 僅澤豐 48 欄舊制（≤11506）有值
        "純xx": pure,              # 僅澤豐 48 欄舊制（≤11506）有值
        "處置費": treatment_single, # 16 欄制有值（澤沛全期間；澤豐 11507 起）
        "調劑費": dispense_nhi,
        "健保小計": int(round(nhi_pre)),
        "健保*0.9": int(round(nhi_part)),
        "掛號費": reg,
        "自費藥粉4項": drug_sum,
        "自費針傷脫": acu_sum,
        "自費檢驗": cash_lab,
        "自費診察": cash_consult,
        "自費其他": cash_other,
        "自費小計": int(round(cash_part)),
        "產值合計": int(round(nhi_part + reg + cash_part)),
    }


def _compute_productivity(
    out_row: dict | None, cash_row: dict | None, clinic_short: str
) -> int:
    return _productivity_breakdown(out_row, cash_row, clinic_short)["產值合計"]


def _salary_gross(sal_row: dict | None) -> int:
    """從 doctor_salary_monthly row 還原應付總額（gross），不含勞健保扣除。"""
    if not sal_row:
        return 0
    return (
        (sal_row.get("director_allowance") or 0)
        + (sal_row.get("session_pay") or 0)
        + (sal_row.get("commission_total") or 0)
        + (sal_row.get("bonus_total") or 0)
        + (sal_row.get("acu_complex_bonus") or 0)
        + (sal_row.get("a91_bonus") or 0)
    )


def _section_doctor_personal_compare(sb):
    """醫師個人業績比較（單月，兩院強制顯示，全院 = 醫師跨院加總）"""
    import altair as alt

    st.divider()
    st.header("👨‍⚕️ 醫師個人業績比較")
    st.caption(
        "本區塊與上半部的「診所」「月份」篩選**獨立**："
        "永遠兩院都顯示；月份單選；「全院」= 醫師跨院加總。"
    )

    # 月份來源：outpatient + visit_stats
    try:
        m_out = [r["service_month"] for r in
                 sb.table("doctor_outpatient_summary").select("service_month").execute().data]
        m_vis = [r["service_month"] for r in
                 sb.table("doctor_visit_stats").select("service_month").execute().data]
    except Exception as e:
        st.error(f"月份載入失敗：{e}")
        return
    months = sorted(_filter_min_month(set(m_out + m_vis)), reverse=True)
    if not months:
        st.info("尚無資料")
        return

    sel_m = st.selectbox(
        "月份（單選）",
        options=months,
        format_func=lambda d: d[:7],
        key="dash_doc_compare_month",
    )

    # 撈該月所有資料
    try:
        clinics_data = sb.table("clinics").select("id, short_name").execute().data
        doctors_data = sb.table("doctors").select("id, name").execute().data
        dc_rows = sb.table("doctor_clinic").select("clinic_id, doctor_id").execute().data
        out_rows = (sb.table("doctor_outpatient_summary").select("*")
                    .eq("service_month", sel_m).execute().data)
        cash_rows = (sb.table("doctor_cash_monthly").select("*")
                     .eq("service_month", sel_m).execute().data)
        visit_rows = (sb.table("doctor_visit_stats").select("*")
                      .eq("service_month", sel_m).execute().data)
        salary_rows = (sb.table("doctor_salary_monthly").select("*")
                       .eq("service_month", sel_m).execute().data)
    except Exception as e:
        st.error(f"資料載入失敗：{e}")
        return

    did_to_name = {d["id"]: d["name"] for d in doctors_data}
    name_to_did = {d["name"]: d["id"] for d in doctors_data}
    fz_id = next((c["id"] for c in clinics_data if c["short_name"] == "澤豐"), None)
    fp_id = next((c["id"] for c in clinics_data if c["short_name"] == "澤沛"), None)

    out_idx = {(r["clinic_id"], r["doctor_id"]): r for r in out_rows}
    cash_idx = {(r["clinic_id"], r["doctor_id"]): r for r in cash_rows}
    visit_idx = {(r["clinic_id"], r["doctor_id"]): r for r in visit_rows}
    sal_idx = {(r["clinic_id"], r["doctor_id"]): r for r in salary_rows}

    # 各院醫師清單：以 doctor_clinic 表配置為基準（含 role='support' 跨院支援），
    # 再 union 4 個資料源做保險。這樣支援醫師即使本月某 source 缺也會出現。
    dc_pairs = {(r["clinic_id"], r["doctor_id"]) for r in dc_rows}
    keys_present = (
        dc_pairs
        | set(out_idx.keys()) | set(visit_idx.keys())
        | set(sal_idx.keys()) | set(cash_idx.keys())
    )
    fz_doctors = sorted({did_to_name[d_id] for (c_id, d_id) in keys_present
                         if c_id == fz_id and d_id in did_to_name})
    fp_doctors = sorted({did_to_name[d_id] for (c_id, d_id) in keys_present
                         if c_id == fp_id and d_id in did_to_name})
    all_doctors = sorted(set(fz_doctors) | set(fp_doctors))

    if not (fz_doctors or fp_doctors):
        st.info(f"{sel_m[:7]} 尚無門診統計或健保人數資料")
        return

    GROUP_COLOR = alt.Scale(
        domain=["澤豐", "澤沛", "全院"],
        range=["#6A5ACD", "#FFA07A", "#3CB371"],
    )

    def _render_grouped_bar(rows: list[dict], y_col: str, y_fmt: str, title: str):
        """通用：3 段（澤豐/澤沛/全院）柱狀圖。rows 須含 顯示/分組/醫師/排序/{y_col}"""
        if not rows:
            st.info(f"{title}：該月份無資料")
            return
        df = pd.DataFrame(rows)
        order = df.sort_values(["排序", "醫師"])["顯示"].tolist()
        ch = alt.Chart(df).mark_bar().encode(
            x=alt.X("顯示:N", sort=order, axis=alt.Axis(labelAngle=-30, title=None)),
            y=alt.Y(f"{y_col}:Q"),
            color=alt.Color("分組:N", scale=GROUP_COLOR),
            tooltip=["分組", "醫師", alt.Tooltip(f"{y_col}:Q", format=y_fmt)],
        ).properties(height=320, title=title)
        st.altair_chart(ch, use_container_width=True)

    # ─── 圖 1：健保平均人次 ─────────────────────
    rows1 = []
    for name in fz_doctors:
        v = visit_idx.get((fz_id, name_to_did[name]))
        if not v: continue
        nhi, s = (v.get("nhi_visits_total") or 0), (v.get("sessions_total") or 0)
        rows1.append({"分組": "澤豐", "醫師": name, "排序": 1,
                      "顯示": f"豐 {name}",
                      "平均人次": round(nhi/s, 2) if s else 0})
    for name in fp_doctors:
        v = visit_idx.get((fp_id, name_to_did[name]))
        if not v: continue
        nhi, s = (v.get("nhi_visits_total") or 0), (v.get("sessions_total") or 0)
        rows1.append({"分組": "澤沛", "醫師": name, "排序": 2,
                      "顯示": f"沛 {name}",
                      "平均人次": round(nhi/s, 2) if s else 0})
    for name in all_doctors:
        d_id = name_to_did[name]
        nhi_sum = sum((visit_idx.get((c, d_id)) or {}).get("nhi_visits_total") or 0
                      for c in (fz_id, fp_id))
        s_sum = sum((visit_idx.get((c, d_id)) or {}).get("sessions_total") or 0
                    for c in (fz_id, fp_id))
        rows1.append({"分組": "全院", "醫師": name, "排序": 3,
                      "顯示": f"全 {name}",
                      "平均人次": round(nhi_sum/s_sum, 2) if s_sum else 0})
    _render_grouped_bar(rows1, "平均人次", ".2f", "📈 健保平均人次（健保總人次 / 診次）")

    # ─── 圖 2：自費銷售額 ─────────────────────
    # 來源：doctor_cash_monthly.cash_total_excl_reg
    #   = 醫師自費統計檔「自費合計」C17 總計列（不含掛號費，兩家統一）
    rows2 = []
    def _cash_total(c_id: int, d_id: int) -> int:
        c = cash_idx.get((c_id, d_id))
        return (c.get("cash_total_excl_reg") if c else 0) or 0

    for name in fz_doctors:
        amt = _cash_total(fz_id, name_to_did[name])
        if amt == 0: continue
        rows2.append({"分組": "澤豐", "醫師": name, "排序": 1,
                      "顯示": f"豐 {name}", "金額": amt})
    for name in fp_doctors:
        amt = _cash_total(fp_id, name_to_did[name])
        if amt == 0: continue
        rows2.append({"分組": "澤沛", "醫師": name, "排序": 2,
                      "顯示": f"沛 {name}", "金額": amt})
    for name in all_doctors:
        d_id = name_to_did[name]
        total = _cash_total(fz_id, d_id) + _cash_total(fp_id, d_id)
        if total == 0: continue
        rows2.append({"分組": "全院", "醫師": name, "排序": 3,
                      "顯示": f"全 {name}", "金額": total})
    _render_grouped_bar(rows2, "金額", ",", "💰 自費銷售額（醫師自費統計「自費合計」總計列；不含掛號費）")

    # ─── 圖 3：產值估算 vs 成本 ─────────────────
    nurse_salary, nurse_sessions = _get_nurse_cost_params(sb)
    base_per_session = nurse_salary / nurse_sessions if nurse_sessions else 0
    st.subheader("⚖️ 醫師產值估算 vs 成本（醫師薪資 + 護理師&助理成本）")
    st.caption(
        f"護理師&助理成本 = 平均薪資({nurse_salary:,.0f}) / 月上班診數({nurse_sessions:,.0f}) "
        f"× 該醫師當月診數 × **人數**\n\n"
        f"人數依當月**該醫師**健保平均人次階梯：≤10 → 1；11–15 → 2；16–30 → 3；>30 → 4"
        f"（可在「⚙️ 系統設定 → 成本參數」調整薪資/診數）"
    )

    rows3: list[dict] = []

    def _nurse_cost_per_clinic(c_id: int, d_id: int) -> tuple[int, int, float, int]:
        """單一 (clinic, doctor) 的護理師&助理成本。
        回傳 (cost, sessions, avg_visits, head_count)
        """
        v = visit_idx.get((c_id, d_id))
        if not v:
            return 0, 0, 0.0, 0
        sess = v.get("sessions_total") or 0
        nhi = v.get("nhi_visits_total") or 0
        avg = (nhi / sess) if sess else 0.0
        count = _nurse_head_count(avg)
        return round(base_per_session * sess * count), sess, avg, count

    SCOPE_PREFIX = {"澤豐": "豐", "澤沛": "沛", "全院": "全"}
    def _entry(scope: str, name: str, prod: int, salary: int, nurse_cost: int):
        rows3.append({
            "分組": scope, "醫師": name,
            "顯示": f"{SCOPE_PREFIX[scope]} {name}",
            "排序": {"澤豐": 1, "澤沛": 2, "全院": 3}[scope],
            "產值": int(prod),
            "醫師薪資": int(salary),
            "護理師&助理成本": int(nurse_cost),
            "成本合計": int(salary + nurse_cost),
        })

    for name in fz_doctors:
        d_id = name_to_did[name]
        prod = _compute_productivity(out_idx.get((fz_id, d_id)),
                                      cash_idx.get((fz_id, d_id)), "澤豐")
        sal = _salary_gross(sal_idx.get((fz_id, d_id)))
        nurse_c, _, _, _ = _nurse_cost_per_clinic(fz_id, d_id)
        if prod == 0 and sal == 0 and nurse_c == 0:
            continue
        _entry("澤豐", name, prod, sal, nurse_c)
    for name in fp_doctors:
        d_id = name_to_did[name]
        prod = _compute_productivity(out_idx.get((fp_id, d_id)),
                                      cash_idx.get((fp_id, d_id)), "澤沛")
        sal = _salary_gross(sal_idx.get((fp_id, d_id)))
        nurse_c, _, _, _ = _nurse_cost_per_clinic(fp_id, d_id)
        if prod == 0 and sal == 0 and nurse_c == 0:
            continue
        _entry("澤沛", name, prod, sal, nurse_c)
    for name in all_doctors:
        d_id = name_to_did[name]
        prod_sum = (
            _compute_productivity(out_idx.get((fz_id, d_id)),
                                   cash_idx.get((fz_id, d_id)), "澤豐")
            + _compute_productivity(out_idx.get((fp_id, d_id)),
                                     cash_idx.get((fp_id, d_id)), "澤沛")
        )
        sal_sum = (_salary_gross(sal_idx.get((fz_id, d_id)))
                   + _salary_gross(sal_idx.get((fp_id, d_id))))
        # 全院護理師成本 = 兩院各自算後加總（人數依各院當月平均人次獨立判定）
        nurse_sum = (_nurse_cost_per_clinic(fz_id, d_id)[0]
                     + _nurse_cost_per_clinic(fp_id, d_id)[0])
        if prod_sum == 0 and sal_sum == 0 and nurse_sum == 0:
            continue
        _entry("全院", name, prod_sum, sal_sum, nurse_sum)

    if rows3:
        df3 = pd.DataFrame(rows3)
        # long format：每位醫師 3 個 row（產值 / 薪資 / 護理師&助理），類型分左右兩柱
        prod_part = df3.assign(類型="產值", 細項="產值",
                               金額=df3["產值"])[["顯示","分組","醫師","排序","類型","細項","金額"]]
        sal_part = df3.assign(類型="成本", 細項="醫師薪資",
                              金額=df3["醫師薪資"])[["顯示","分組","醫師","排序","類型","細項","金額"]]
        nur_part = df3.assign(類型="成本", 細項="護理師&助理成本",
                              金額=df3["護理師&助理成本"])[["顯示","分組","醫師","排序","類型","細項","金額"]]
        long3 = pd.concat([prod_part, sal_part, nur_part], ignore_index=True)
        order3 = df3.sort_values(["排序","醫師"])["顯示"].tolist()

        ch3 = alt.Chart(long3).mark_bar().encode(
            x=alt.X("顯示:N", sort=order3, axis=alt.Axis(labelAngle=-30, title=None)),
            xOffset=alt.XOffset("類型:N", sort=["產值", "成本"]),
            y=alt.Y("金額:Q", stack="zero"),
            color=alt.Color("細項:N", scale=alt.Scale(
                domain=["產值", "醫師薪資", "護理師&助理成本"],
                range=["#6A5ACD", "#3CB371", "#FFA07A"],
            )),
            tooltip=["分組", "醫師", "細項", alt.Tooltip("金額:Q", format=",")],
        ).properties(height=420)
        st.altair_chart(ch3, use_container_width=True)

        with st.expander("📋 產值/成本明細表", expanded=False):
            view = df3[["分組", "醫師", "產值", "醫師薪資", "護理師&助理成本", "成本合計"]].copy()
            view["產值-成本"] = view["產值"] - view["成本合計"]
            for c in ("產值", "醫師薪資", "護理師&助理成本", "成本合計", "產值-成本"):
                view[c] = view[c].map(lambda v: f"{v:,}")
            st.dataframe(view, use_container_width=True, hide_index=True)

        # 產值估算逐項拆解（對齊手算用）
        with st.expander("🔬 產值估算分項拆解（對照手算）", expanded=False):
            st.caption(
                "公式：(健保項目)×0.9 + 掛號費 + 自費藥粉×0.3 + 自費針傷脫×0.4 + "
                "自費檢驗×0.8 + 自費診察 + 自費其他"
            )
            bd_rows = []
            for c_id, c_short in [(fz_id, "澤豐"), (fp_id, "澤沛")]:
                for name in (fz_doctors if c_short == "澤豐" else fp_doctors):
                    d_id = name_to_did[name]
                    o = out_idx.get((c_id, d_id))
                    c = cash_idx.get((c_id, d_id))
                    if not o and not c:
                        continue
                    bd = _productivity_breakdown(o, c, c_short)
                    bd_rows.append({"分組": c_short, "醫師": name, **bd})
            if bd_rows:
                st.dataframe(pd.DataFrame(bd_rows),
                             use_container_width=True, hide_index=True)

        # 護理師人數判定明細（透明化）
        with st.expander("👩‍⚕️ 護理師&助理人數判定（依平均人次階梯）", expanded=False):
            head_rows = []
            for c_id, c_short in [(fz_id, "澤豐"), (fp_id, "澤沛")]:
                for name in (fz_doctors if c_short == "澤豐" else fp_doctors):
                    d_id = name_to_did[name]
                    cost, sess, avg, count = _nurse_cost_per_clinic(c_id, d_id)
                    if sess == 0 and cost == 0:
                        continue
                    head_rows.append({
                        "診所": c_short, "醫師": name,
                        "診數": sess,
                        "平均人次": round(avg, 2),
                        "人數": count,
                        "成本": f"{cost:,}",
                    })
            if head_rows:
                st.dataframe(pd.DataFrame(head_rows),
                             use_container_width=True, hide_index=True)
    else:
        st.info("該月份無產值/成本資料")

    # ─── 圖 4：合理門診量（5 階段堆疊）───────────────
    st.subheader("📊 合理門診量（五階段堆疊）")
    st.caption(
        "下→上：1-30 / 31-50 / 51-70 / 71-150 / 151-1000 人次。"
        "「（支援）」柱 = 該診所支援醫師；其合理量在來源檔掛在各正職的"
        "「補支援醫師數」，匯入時從末階段往前扣並歸給支援醫師。"
    )
    try:
        cap_rows = (sb.table("doctor_capacity_stage").select("*")
                    .eq("service_month", sel_m).execute().data)
    except Exception as e:
        cap_rows = []
        st.warning(f"合理門診量資料載入失敗（表可能未建立）：{e}")

    if not cap_rows:
        st.info(f"{sel_m[:7]} 尚無合理門診量資料；請至「📥 本月資料匯入 → 合理門診量」上傳")
    else:
        cap_idx = {(r["clinic_id"], r["doctor_id"]): r for r in cap_rows}
        STAGE_NAMES = ["1-30", "31-50", "51-70", "71-150", "151-1000"]
        STAGE_COLORS = ["#4B0082", "#6A5ACD", "#9370DB", "#BA55D3", "#DDA0DD"]
        rows4: list[dict] = []

        def _add_cap(scope: str, name: str, stages: list[int],
                     is_support: bool = False):
            if sum(stages) == 0:
                return
            label = f"{name}（支援）" if is_support else name
            for i, stg in enumerate(stages):
                rows4.append({
                    "分組": scope, "醫師": label,
                    "顯示": f"{SCOPE_PREFIX[scope]} {label}",
                    "排序": {"澤豐": 1, "澤沛": 2}[scope],
                    "支援序": 1 if is_support else 0,
                    "階段": STAGE_NAMES[i],
                    "階段序": i,
                    "人次": int(stg),
                })

        def _stages_of(c_id: int, d_id: int) -> list[int]:
            r = cap_idx.get((c_id, d_id))
            if not r:
                return [0] * 5
            return [r.get(f"stage{i+1}") or 0 for i in range(5)]

        # 各診所柱體 = 主聘 + 支援（is_support 列來自匯入時的「補支援醫師數」歸屬）；
        # 名單以醫師配置 union 該月 doctor_capacity_stage 實有列，避免漏柱
        for c_id, scope, base_names in (
            (fz_id, "澤豐", fz_doctors), (fp_id, "澤沛", fp_doctors),
        ):
            cap_names = {did_to_name[d] for (cc, d) in cap_idx
                         if cc == c_id and d in did_to_name}
            for name in sorted(set(base_names) | cap_names):
                d_id = name_to_did[name]
                r = cap_idx.get((c_id, d_id))
                _add_cap(scope, name, _stages_of(c_id, d_id),
                         is_support=bool(r and r.get("is_support")))

        if rows4:
            df4 = pd.DataFrame(rows4)
            order4 = (df4[["顯示", "排序", "支援序", "醫師"]].drop_duplicates()
                      .sort_values(["排序", "支援序", "醫師"])["顯示"].tolist())
            ch4 = alt.Chart(df4).mark_bar().encode(
                x=alt.X("顯示:N", sort=order4,
                        axis=alt.Axis(labelAngle=-30, title=None)),
                y=alt.Y("人次:Q", stack="zero"),
                color=alt.Color("階段:N",
                    scale=alt.Scale(domain=STAGE_NAMES, range=STAGE_COLORS),
                    sort=STAGE_NAMES,
                ),
                order=alt.Order("階段序:Q", sort="ascending"),
                tooltip=["分組", "醫師", "階段",
                        alt.Tooltip("人次:Q", format=",")],
            ).properties(height=400)
            st.altair_chart(ch4, use_container_width=True)

            with st.expander("📋 合理門診量明細", expanded=False):
                wide = (df4.pivot_table(
                    index=["分組", "醫師"], columns="階段",
                    values="人次", aggfunc="sum", fill_value=0)
                    .reindex(columns=STAGE_NAMES).reset_index())
                wide["合計"] = wide[STAGE_NAMES].sum(axis=1)
                st.dataframe(wide, use_container_width=True, hide_index=True)
        else:
            st.info("該月份合理門診量全為 0")


# ============================================================
# 2. 月度實帳金流分析（Phase 4 實帳金流）
# ============================================================


def _render_data_health(sb, service_month: str):
    """
    顯示該月份所有資料來源的筆數，0 筆會 ⚠️ 警告。
    用途：跨月切換時，院長可一眼看出某月某帳戶/某資料源是否缺資料，
          避免誤判為系統 bug。
    完整度判定邏輯抽到 data_processor.data_health（與財報列印共用）。
    """
    from data_processor.data_health import compute_cashflow_health

    sm_label = service_month[:7]
    health = compute_cashflow_health(sb, service_month)
    issues = health["issues"]
    bank_table = health["bank_table"]
    other_rows = health["other_rows"]

    with st.expander(
        f"🩺 {sm_label} 資料完整度診斷"
        + (f"（⚠️ {len(issues)} 項缺資料）" if issues else "（✅ 全到位）"),
        expanded=bool(issues),
    ):
        st.markdown("**銀行交易（bank_transactions）：**")
        st.dataframe(pd.DataFrame(bank_table), use_container_width=True, hide_index=True)
        st.markdown("**其他資料源：**")
        st.dataframe(pd.DataFrame(other_rows), use_container_width=True, hide_index=True)
        if issues:
            st.markdown("**⚠️ 待補資料清單：**")
            for msg in issues:
                st.markdown(f"- {msg}")


def page_overview():
    st.title("💰 月度實帳金流分析")

    from data_processor.monthly_pl import (
        calculate_both_clinics, calculate_check_expense_month,
        list_available_months,
    )

    st.caption(
        "🗓️ **實帳模式**：N 月帳上實際發生的款項記在 N 月。"
        "標註「歸屬：前月」的款項屬於前月損益（之後另做月度損益分析），"
        "**不**影響本月實帳合計。"
    )

    sb = get_authed_client()
    months = list_available_months(sb)
    if not months:
        st.warning("⚠️ 尚無銀行交易資料。請先上傳玉山+中信 CSV。")
        return

    col1, _ = st.columns([2, 5])
    with col1:
        service_month = st.selectbox(
            "月份", months, format_func=lambda d: d[:7], key="pl_month",
        )

    version = _data_version(sb)
    with st.spinner("計算中..."):
        pl_fz, pl_fp = _cached_both_clinics(sb, service_month, version)
        check = _cached_check_month(sb, service_month, version)

    # ════════════════════════════════════════════════════════
    # 0. 資料完整度診斷（讓院長一眼看到該月缺什麼）
    # ════════════════════════════════════════════════════════
    _render_data_health(sb, service_month)

    # ════════════════════════════════════════════════════════
    # 1. 澤豐中醫診所實帳收支（總院，置頂）
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("# 🏥 澤豐中醫診所實帳收支")

    k1, k2, k3 = st.columns(3)
    k1.metric("總收入", f"NT$ {pl_fz.total_income:,}")
    k2.metric("總支出", f"NT$ {pl_fz.total_expense:,}")
    k3.metric(
        "淨利", f"NT$ {pl_fz.net:,}",
        delta=(
            f"{pl_fz.net/pl_fz.total_income:.1%}"
            if pl_fz.total_income else None
        ),
    )

    _BANK_COLS = [
        "transaction_date", "summary", "counterparty",
        "amount", "note", "attribution_month",
    ]

    def _show_items(items, columns=None):
        if not items:
            return
        df = pd.DataFrame(items)
        if columns:
            cols = [c for c in columns if c in df.columns]
            df = df[cols]
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption(
        "玉山健保戶逐筆，排除轉到周院長個人 668979072975 與澤豐自家中信 0347940007803；"
        "中信只取 x6 豐沛金流入帳 + x8 現金存入；"
        "隱形支出 x3 / x9 / x12 / x13；x10 手 KEY 逐筆。"
    )

    st.markdown("### 📥 收入")
    fz_in_summary = pd.DataFrame([
        {"項目": "玉山逐筆入帳", "小計": pl_fz.esun_inflow_total, "歸屬": "本月"},
        {"項目": "x6 豐沛金流（澤沛→澤豐）", "小計": pl_fz.x6_fengpei_settle, "歸屬": "前月"},
        {"項目": "x8 澤豐現金入帳", "小計": pl_fz.x8_zefeng_cash_revenue, "歸屬": "前月"},
        {"項目": "x10 手 KEY 收入", "小計": pl_fz.x10_income_total, "歸屬": "本月"},
    ])
    st.dataframe(fz_in_summary, use_container_width=True, hide_index=True)

    if pl_fz.esun_inflow_items:
        with st.expander(f"📑 玉山入帳明細（{len(pl_fz.esun_inflow_items)} 筆）"):
            _show_items(pl_fz.esun_inflow_items, _BANK_COLS)
    if pl_fz.x6_items:
        with st.expander(f"📑 x6 豐沛金流明細（{len(pl_fz.x6_items)} 筆，歸屬前月）"):
            _show_items(pl_fz.x6_items, _BANK_COLS)
    if pl_fz.x8_items:
        with st.expander(f"📑 x8 現金存入明細（{len(pl_fz.x8_items)} 筆，歸屬前月）"):
            _show_items(pl_fz.x8_items, _BANK_COLS)
    if pl_fz.x8_unmatched_items:
        with st.expander(
            f"⚠️ 中信「現金/存款機」入帳但無 manual_annotation 對應"
            f"（{len(pl_fz.x8_unmatched_items)} 筆，視為個人存款未認列 x8）"
        ):
            st.caption(
                "若這幾筆其實是診所收入，請到「資料匯入區 → 金流補充備註」"
                "新增記錄：scope=診所 / form=存現 / account=澤豐&個人中信 / "
                "amount 與此處相符；description 寫「11XYY 澤豐現金收入」"
                "（XYY=歸屬月）系統會自動抓歸屬月份。"
            )
            _show_items(pl_fz.x8_unmatched_items, _BANK_COLS)
    if pl_fz.x10_income_items:
        with st.expander(f"📑 x10 手 KEY 收入明細（{len(pl_fz.x10_income_items)} 筆）"):
            _show_items(pl_fz.x10_income_items)

    st.markdown("### 📤 支出")
    fz_ex_summary = pd.DataFrame([
        {"項目": "玉山逐筆出帳", "小計": pl_fz.esun_outflow_total, "歸屬": "本月"},
        {"項目": "x3 澤豐現金支出", "小計": pl_fz.x3_zefeng_cash_expense, "歸屬": "本月"},
        {"項目": "x9 謝松坊薪資", "小計": pl_fz.x9_offsite_staff_pay, "歸屬": "前月"},
        {"項目": "x10 手 KEY 支出", "小計": pl_fz.x10_expense_total, "歸屬": "本月"},
        {"項目": "x12 澤豐合約支出", "小計": pl_fz.x12_zefeng_contract_expense, "歸屬": "本月"},
        {"項目": "x13 周院長薪資（兩院總和）", "小計": pl_fz.x13_zhou_doctor_salary, "歸屬": "前月"},
        {"項目": "x14 醫師薪資現金給付", "小計": getattr(pl_fz, "x14_cash_salary_pay", 0), "歸屬": "前月"},
    ])
    st.dataframe(fz_ex_summary, use_container_width=True, hide_index=True)

    if pl_fz.esun_outflow_items:
        with st.expander(f"📑 玉山出帳明細（{len(pl_fz.esun_outflow_items)} 筆）"):
            _show_items(pl_fz.esun_outflow_items, _BANK_COLS)
    if pl_fz.x3_items:
        with st.expander(
            f"📑 x3 澤豐現金支出明細（本月 {len(pl_fz.x3_items)} 筆，"
            f"合計 NT$ {pl_fz.x3_zefeng_cash_expense:,}）"
        ):
            _show_items(pl_fz.x3_items)
    if pl_fz.x9_items:
        with st.expander("📑 x9 謝松坊薪資明細（歸屬前月）"):
            _show_items(pl_fz.x9_items)
    if pl_fz.x10_expense_items:
        with st.expander(f"📑 x10 手 KEY 支出明細（{len(pl_fz.x10_expense_items)} 筆）"):
            _show_items(pl_fz.x10_expense_items)
    if pl_fz.x12_items:
        with st.expander(f"📑 x12 澤豐合約支出明細（{len(pl_fz.x12_items)} 筆）"):
            _show_items(pl_fz.x12_items)
    if pl_fz.x13_items:
        with st.expander("📑 x13 周院長薪資明細（歸屬前月）"):
            _show_items(pl_fz.x13_items)
    if getattr(pl_fz, "x14_items", None):
        with st.expander(
            f"📑 x14 醫師薪資現金給付明細（{len(pl_fz.x14_items)} 筆）"
        ):
            st.caption(
                "來自罐頭「澤豐現金收入」的現金給薪扣除：現金收入記總額、"
                "存入銀行前先扣的醫師薪資列此為支出（淨額 = 實際存入）。"
            )
            _show_items(pl_fz.x14_items)
    if getattr(pl_fz, "private_unmatched_items", None):
        st.error(
            f"🚨 有 {len(pl_fz.private_unmatched_items)} 筆「院長私人帳務」"
            "標記**沒配對到任何銀行交易**，該筆金流仍被計入收支！"
            "常見原因：①實際扣款含跨行手續費（如 KEY 3,000,000 實扣 "
            "3,000,040，差額 ≤50 且同日可自動吸收，其餘須改 KEY 成實扣金額）"
            "②單筆限額被拆成多筆轉帳（一筆標記只排一筆，須分開 KEY）"
            "③日期/帳戶選錯。請打開下方玉山出入帳明細核對實際金額後修正備註。"
        )
        st.dataframe(pd.DataFrame([
            {"日期": r.get("entry_date"), "形式": r.get("form"),
             "金額": r.get("amount"), "帳戶": r.get("account"),
             "備註": r.get("description")}
            for r in pl_fz.private_unmatched_items
        ]), use_container_width=True, hide_index=True)
    if getattr(pl_fz, "private_excluded_items", None):
        with st.expander(
            f"🔴 院長私人帳務排除明細"
            f"（{len(pl_fz.private_excluded_items)} 筆，不入收入/支出）"
        ):
            st.caption(
                "手 KEY「院長私人帳務」標記命中的銀行交易，"
                "已完全排除於實帳金流與損益分析。"
            )
            _show_items(
                pl_fz.private_excluded_items,
                ["transaction_date", "summary", "counterparty",
                 "direction", "amount", "private_note"],
            )

    # ════════════════════════════════════════════════════════
    # 2. 澤沛中醫診所實帳收支
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("# 🏥 澤沛中醫診所實帳收支")

    k1, k2, k3 = st.columns(3)
    k1.metric("總收入", f"NT$ {pl_fp.total_income:,}")
    k2.metric("總支出", f"NT$ {pl_fp.total_expense:,}")
    k3.metric(
        "淨利", f"NT$ {pl_fp.net:,}",
        delta=(
            f"{pl_fp.net/pl_fp.total_income:.1%}"
            if pl_fp.total_income else None
        ),
    )

    st.caption(
        "玉山 + 中信進出戶逐筆全記（澤沛沒混到私人財務）。"
        "中信 3 筆固定 settle（x5 現金 / x6 豐沛金流 / x7 合約）已標註分類，"
        "歸屬前月；月份用實際出帳日（不再用標籤月）。"
    )

    st.markdown("### 📥 收入")
    fp_in_summary = pd.DataFrame([
        {"項目": "玉山逐筆入帳", "小計": pl_fp.esun_inflow_total, "歸屬": "本月"},
        {"項目": "中信逐筆入帳", "小計": pl_fp.ctbc_inflow_total, "歸屬": "本月"},
        {"項目": "x10 手 KEY 收入", "小計": pl_fp.x10_income_total, "歸屬": "本月"},
    ])
    st.dataframe(fp_in_summary, use_container_width=True, hide_index=True)

    if pl_fp.esun_inflow_items:
        with st.expander(f"📑 玉山入帳明細（{len(pl_fp.esun_inflow_items)} 筆）"):
            _show_items(pl_fp.esun_inflow_items, _BANK_COLS)
    if pl_fp.ctbc_inflow_items:
        with st.expander(f"📑 中信入帳明細（{len(pl_fp.ctbc_inflow_items)} 筆）"):
            _show_items(pl_fp.ctbc_inflow_items, _BANK_COLS)
    if pl_fp.x10_income_items:
        with st.expander(f"📑 x10 手 KEY 收入明細（{len(pl_fp.x10_income_items)} 筆）"):
            _show_items(pl_fp.x10_income_items)

    st.markdown("### 📤 支出")
    fp_ex_summary = pd.DataFrame([
        {"項目": "玉山逐筆出帳", "小計": pl_fp.esun_outflow_total, "歸屬": "本月"},
        {"項目": "中信逐筆出帳（含 3 筆 settle）", "小計": pl_fp.ctbc_outflow_total, "歸屬": "本月"},
        {"項目": "　└ x5 現金結算（→周院長）", "小計": pl_fp.cash_settle_outflow, "歸屬": "前月"},
        {"項目": "　└ x6 豐沛金流（→澤豐）", "小計": pl_fp.fengpei_outflow, "歸屬": "前月"},
        {"項目": "　└ x7 合約結算（→周院長）", "小計": pl_fp.contract_settle_outflow, "歸屬": "前月"},
        {"項目": "x10 手 KEY 支出", "小計": pl_fp.x10_expense_total, "歸屬": "本月"},
        {"項目": "醫師薪資現金給付", "小計": getattr(pl_fp, "cash_salary_pay_total", 0), "歸屬": "前月"},
    ])
    st.dataframe(fp_ex_summary, use_container_width=True, hide_index=True)
    st.caption("ℹ️ x5 / x6 / x7 已包含於「中信逐筆出帳」中，分類項僅供識別不重複加總。")
    if getattr(pl_fp, "cash_salary_pay_items", None):
        with st.expander(
            f"📑 醫師薪資現金給付明細（{len(pl_fp.cash_salary_pay_items)} 筆）"
        ):
            st.caption(
                "來自罐頭「澤沛現金收入」的現金給薪扣除：現金收入記總額、"
                "存入銀行前先扣的醫師薪資列此為支出（淨額 = 實際存入）。"
            )
            _show_items(pl_fp.cash_salary_pay_items)
    if getattr(pl_fp, "private_unmatched_items", None):
        st.error(
            f"🚨 有 {len(pl_fp.private_unmatched_items)} 筆「院長私人帳務」"
            "標記沒配對到任何銀行交易，該筆金流仍被計入收支！"
            "請核對實際扣款金額（含手續費）、日期與帳戶後修正備註。"
        )
        st.dataframe(pd.DataFrame([
            {"日期": r.get("entry_date"), "形式": r.get("form"),
             "金額": r.get("amount"), "帳戶": r.get("account"),
             "備註": r.get("description")}
            for r in pl_fp.private_unmatched_items
        ]), use_container_width=True, hide_index=True)
    if getattr(pl_fp, "private_excluded_items", None):
        with st.expander(
            f"🔴 院長私人帳務排除明細"
            f"（{len(pl_fp.private_excluded_items)} 筆，不入收入/支出）"
        ):
            st.caption(
                "手 KEY「院長私人帳務」標記命中的銀行交易，"
                "已完全排除於實帳金流與損益分析。"
            )
            _show_items(
                pl_fp.private_excluded_items,
                ["transaction_date", "summary", "counterparty",
                 "direction", "amount", "private_note"],
            )

    if pl_fp.esun_outflow_items:
        with st.expander(f"📑 玉山出帳明細（{len(pl_fp.esun_outflow_items)} 筆）"):
            _show_items(pl_fp.esun_outflow_items, _BANK_COLS)
    if pl_fp.ctbc_outflow_items:
        with st.expander(f"📑 中信出帳明細（{len(pl_fp.ctbc_outflow_items)} 筆，含 settle 標註）"):
            _show_items(
                pl_fp.ctbc_outflow_items,
                ["transaction_date", "summary", "counterparty", "amount", "note", "settle_kind", "attribution_month"],
            )
    # x5 現金結算的前月逐筆明細（cash_expense，由澤沛現金支出檔上傳）
    if pl_fp.x5_detail_items:
        with st.expander(
            f"📑 x5 澤沛現金結算明細（前月現金支出 {len(pl_fp.x5_detail_items)} 筆，"
            f"合計 NT$ {pl_fp.x5_detail_total:,}）"
        ):
            if (pl_fp.cash_settle_outflow
                    and pl_fp.x5_detail_total != pl_fp.cash_settle_outflow):
                st.caption(
                    f"⚠️ 明細合計 {pl_fp.x5_detail_total:,} ≠ 本月 x5 結算轉帳 "
                    f"{pl_fp.cash_settle_outflow:,}"
                    f"（差 {pl_fp.cash_settle_outflow - pl_fp.x5_detail_total:+,}）"
                    "— 結算可能含零頭調整或明細檔未更新。"
                )
            st.caption("ℹ️ 明細僅供對帳；金額已由中信 x5 結算交易入帳，不重複加總。")
            _show_items(pl_fp.x5_detail_items)
    elif pl_fp.cash_settle_outflow:
        st.caption(
            "ℹ️ x5 現金結算尚無前月逐筆明細 — 請至「📥 本月資料匯入 → "
            "現金支出（含支票自動分流）」選「澤沛」上傳現金支出檔。"
        )
    if pl_fp.x10_expense_items:
        with st.expander(f"📑 x10 手 KEY 支出明細（{len(pl_fp.x10_expense_items)} 筆）"):
            _show_items(pl_fp.x10_expense_items)

    # ── 澤沛合夥人財務報表（給胡舒婷醫師，開新分頁）──
    st.markdown("### 🤝 澤沛合夥人財務報表")
    st.caption(
        "美化版新分頁呈現，可直接列印或下載傳給胡醫師（不含支票獨立項目）。"
        "**月度**：收入/支出細項 + 逐筆明細（含前月現金結算細項）+ 月度淨利趨勢。"
        "**季度**：本季各月收入/支出/淨利 + 近 12 月月度淨利趨勢 + 近 6 季季度淨利趨勢。"
    )
    from datetime import datetime as _dt
    from data_processor import report_builder as rb

    def _render_partner_output(state_key: str, widget_key: str, fname: str):
        html_cached = st.session_state.get(state_key)
        if not html_cached:
            return
        _open_in_new_tab_widget(html_cached, key=widget_key)
        st.download_button(
            "⬇️ 下載 HTML（備援，可用 LINE/Email 傳給胡醫師）",
            data=html_cached.encode("utf-8"),
            file_name=fname, mime="text/html", key=f"{widget_key}_dl",
        )
        with st.expander("👁️ 內嵌預覽（外觀以新分頁為準）"):
            import streamlit.components.v1 as components
            components.html(html_cached, height=700, scrolling=True)

    # 月度
    if st.button(
        f"📄 產生 {service_month[:7]} 月度報表",
        key="fp_partner_gen",
    ):
        with st.spinner("產生月度報表中..."):
            try:
                html_str, err = rb.build_zepei_partner_monthly(
                    sb, service_month, months,
                    _dt.now().strftime("%Y-%m-%d %H:%M"),
                )
            except Exception as e:
                html_str, err = None, f"產生報表失敗:{e}"
        if err:
            st.warning(err, icon="⚠️")
            st.session_state.pop("fp_partner_html", None)
        else:
            st.session_state["fp_partner_html"] = html_str
            st.session_state["fp_partner_month"] = service_month
    if st.session_state.get("fp_partner_month") == service_month:
        _render_partner_output(
            "fp_partner_html", "fp_partner",
            f"澤沛財務報表_{service_month[:7]}.html",
        )

    # 季度
    q_col1, q_col2 = st.columns([2, 3])
    quarters = sorted({rb.quarter_of(m) for m in months}, reverse=True)
    cur_q = rb.quarter_of(service_month)
    with q_col1:
        sel_q = st.selectbox(
            "季度", quarters,
            index=quarters.index(cur_q) if cur_q in quarters else 0,
            format_func=lambda yq: f"{yq[0]} 第 {yq[1]} 季",
            key="fp_partner_q_sel",
        )
    with q_col2:
        st.write("")  # 對齊
        gen_q = st.button(
            f"📄 產生 {sel_q[0]} Q{sel_q[1]} 季度報表",
            key="fp_partner_q_gen",
        )
    if gen_q:
        with st.spinner("產生季度報表中..."):
            try:
                html_q, err_q = rb.build_zepei_partner_quarterly(
                    sb, sel_q[0], sel_q[1], months,
                    _dt.now().strftime("%Y-%m-%d %H:%M"),
                )
            except Exception as e:
                html_q, err_q = None, f"產生報表失敗:{e}"
        if err_q:
            st.warning(err_q, icon="⚠️")
            st.session_state.pop("fp_partner_q_html", None)
        else:
            st.session_state["fp_partner_q_html"] = html_q
            st.session_state["fp_partner_q_key"] = sel_q
    if st.session_state.get("fp_partner_q_key") == sel_q:
        _render_partner_output(
            "fp_partner_q_html", "fp_partner_q",
            f"澤沛財務報表_{sel_q[0]}Q{sel_q[1]}.html",
        )

    # ════════════════════════════════════════════════════════
    # 3. 支票支出（兩家共用，獨立項目，不入合計）
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("# 🧾 支票支出（獨立項目）")
    st.caption(
        "兩家診所共用支票戶（玉山+中信）。獨立顯示，**不入兩家收支淨利合計**。"
        "院長後續可能用此替換澤豐合約支出做 B 版總院月平衡表。"
    )

    k1, k2 = st.columns(2)
    k1.metric("當月支票合計", f"NT$ {check.total:,}")
    k2.metric("筆數", f"{len(check.raw_items)}")

    if check.raw_items:
        with st.expander("📑 支票明細（廠商、銀行、金額）"):
            df_chk = pd.DataFrame(check.raw_items)
            cols = [c for c in ["vendor", "amount", "bank", "note"] if c in df_chk.columns]
            st.dataframe(df_chk[cols], use_container_width=True, hide_index=True)

            st.markdown("**按廠商：**")
            vd = pd.DataFrame(
                [{"廠商": v, "合計": a} for v, a in check.by_vendor.items()]
            ).sort_values("合計", ascending=False)
            st.dataframe(vd, use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════
    # 4. 12 月淨利趨勢（不含支票）
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("## 📈 月度淨利趨勢（合計不含支票，支票獨立）")
    trend_data = []
    chk_data = []
    for m in sorted(months)[-12:]:
        try:
            tfz, tfp = _cached_both_clinics(sb, m, version)
            tchk = _cached_check_month(sb, m, version)
            trend_data.append({
                "月份": m[:7],
                "澤豐淨利": tfz.net,
                "澤沛淨利": tfp.net,
                "合計（不含支票）": tfz.net + tfp.net,
            })
            chk_data.append({"月份": m[:7], "支票支出": tchk.total})
        except Exception:
            continue

    if trend_data:
        import altair as alt
        df_t = pd.DataFrame(trend_data)
        df_long = df_t.melt(
            id_vars=["月份"],
            value_vars=["澤豐淨利", "澤沛淨利", "合計（不含支票）"],
            var_name="診所", value_name="淨利",
        )
        chart = alt.Chart(df_long).mark_line(point=True).encode(
            x=alt.X("月份:N", sort="ascending"),
            y=alt.Y("淨利:Q"),
            color="診所:N",
            tooltip=["月份", "診所", alt.Tooltip("淨利:Q", format=",")],
        ).properties(height=300, title="淨利趨勢")
        st.altair_chart(chart, use_container_width=True)

        if chk_data:
            df_chk = pd.DataFrame(chk_data)
            chart_chk = alt.Chart(df_chk).mark_bar(color="#FFA07A").encode(
                x=alt.X("月份:N", sort="ascending"),
                y=alt.Y("支票支出:Q"),
                tooltip=["月份", alt.Tooltip("支票支出:Q", format=",")],
            ).properties(height=200, title="支票支出（獨立）")
            st.altair_chart(chart_chk, use_container_width=True)

    with st.expander("ℹ️ 計算規則說明"):
        st.markdown("""
**大前提（院長 2026-05-05）**：

3 個收支主體：周院長個人 / 澤豐中醫診所 / 澤沛中醫診所。
本頁只算澤豐、澤沛兩家診所的「實帳收支」（N 月帳上發生的款項記在 N 月）。
跨月歸屬的款項會標註「歸屬：前月」，**不**在本月合計，未來再做月度損益分析。

---

**澤豐中醫診所**：
- 收入 = 玉山逐筆入帳 + x6 豐沛金流 + x8 現金存入 + x10 手 KEY 收入
- 支出 = 玉山逐筆出帳 + x3 澤豐現金支出 + x9 謝松坊薪資 + x10 手 KEY 支出 + x12 澤豐合約 + x13 周院長薪資
- ⛔ 玉山戶排除：轉到 668979072975（周院長個人）、轉到 0347940007803（澤豐自家中信，內部移轉）
- ⛔ 中信混戶不全抓：只取 x6（澤沛→澤豐豐沛金流）+ x8（現金存入）。其他款項視為周院長個人或無法歸屬
- 「歸屬前月」：x6 / x8 / x9 / x13（當月帳但實質屬前月損益）

**澤沛中醫診所**：
- 玉山 + 中信進出戶逐筆全記（澤沛沒混到私人財務）
- 中信 3 筆固定 settle 用 note 關鍵字分類：x5 現金支出 / x6 豐沛金流 / x7 合約
- 月份用 transaction_date 當月（不再用標籤月歸屬）
- x4（澤沛 N-1 月現金支出，由周院長代墊）— 屬周院長個人，不在此記

**支票支出**：兩家共用，獨立顯示，不入合計趨勢。
""")


# ============================================================
# 2b. 月度損益分析（Phase 4b 會計精神損益）
# ============================================================
def page_monthly_pl():
    st.title("📈 月度損益分析")

    from data_processor.monthly_profit_loss import list_available_months

    st.caption(
        "🧾 **會計精神**：N 月「歸屬」的款項記在 N 月（與「月度實帳金流分析」不同）。"
        "支付通常延後（4 月轉出歸 3 月），健保給付以備註月份為準，"
        "整復推拿用『只記帳』條目獨立統計。"
    )

    st.caption(
        "ℹ️ 損益需用「下個月」銀行明細回推，故只顯示**資料完整**的月份"
        "（下月玉山+中信 CSV 已上傳、本月健保第一筆給付已入帳）。"
    )

    sb = get_authed_client()
    months = list_available_months(sb)
    if not months:
        st.warning("⚠️ 尚無可分析的月份資料")
        return

    version = _data_version(sb)
    # 完整度只檢查近 15 個月（cheap count；快取）
    candidates = months[:15]
    health = {m: _cached_pl_health(sb, m, version) for m in candidates}
    complete_fz = [m for m in candidates if health[m]["complete_fz"]]
    complete_fp = [m for m in candidates if health[m]["complete_fp"]]

    # ── 資料完整度診斷 ──
    with st.expander(
        "🩺 月度損益 資料完整度診斷（近 6 個月）", expanded=False,
    ):
        diag_rows = []
        issue_lines: list[str] = []
        for m in candidates[:6]:
            h = health[m]
            diag_rows.append({
                "月份": m[:7],
                "澤豐": "✅ 完整" if h["complete_fz"] else f"⚠️ 缺 {len(h['issues_fz'])} 項",
                "澤沛": "✅ 完整" if h["complete_fp"] else f"⚠️ 缺 {len(h['issues_fp'])} 項",
            })
            for i in h["issues_fz"] + h["issues_fp"]:
                issue_lines.append(f"{m[:7]}：{i}")
        st.dataframe(pd.DataFrame(diag_rows), use_container_width=True,
                     hide_index=True)
        if issue_lines:
            st.markdown("**⚠️ 待補資料：**")
            for ln in issue_lines:
                st.markdown(f"- {ln}")
        st.caption("資料不完整的月份不會出現在下方分析與「財報列印」。")

    if not complete_fz and not complete_fp:
        st.warning(
            "⚠️ 目前沒有資料完整的月份可分析。"
            "請先上傳「下個月」的玉山+中信 CSV 與本月健保給付資料。"
        )
        return

    max_n = min(12, max(len(complete_fz), len(complete_fp), 1))
    col1, col2 = st.columns([3, 1])
    with col1:
        if max_n > 1:
            chosen_n = st.slider(
                "顯示最近幾個完整月份", min_value=1, max_value=max_n,
                value=min(3, max_n),
            )
        else:
            chosen_n = 1
            st.caption("目前僅 1 個完整月份")
    with col2:
        st.metric("完整月份數", f"{max(len(complete_fz), len(complete_fp))}")

    fz_tbl, fz_chart = complete_fz[:chosen_n], complete_fz[:6]
    fp_tbl, fp_chart = complete_fp[:chosen_n], complete_fp[:6]
    need = sorted(set(fz_tbl) | set(fz_chart) | set(fp_tbl) | set(fp_chart),
                  reverse=True)

    by_clinic_month: dict[tuple[str, str], any] = {}
    with st.spinner(f"計算 {len(need)} 個月 × 2 診所..."):
        for sm in need:
            try:
                pl_fz, pl_fp = _cached_both_pl(sb, sm, version)
                by_clinic_month[("澤豐", sm)] = pl_fz
                by_clinic_month[("澤沛", sm)] = pl_fp
            except Exception as e:
                st.error(f"{sm} 計算失敗：{e}")

    # 每家診所一張表 + 比較柱狀圖（各自只顯示該診所完整月份）
    for clinic_short, tbl_months, chart_m in (
        ("澤豐", fz_tbl, fz_chart), ("澤沛", fp_tbl, fp_chart),
    ):
        st.subheader(f"🏥 {clinic_short} 月度損益")
        if not tbl_months:
            st.info(f"{clinic_short}：目前無資料完整的月份")
            continue
        _render_pl_table(by_clinic_month, clinic_short, tbl_months)
        _render_pl_charts(by_clinic_month, clinic_short, chart_m)
        _render_pl_breakdown(by_clinic_month, clinic_short, tbl_months)


def _fmt_amt(v) -> str:
    if v is None:
        return "—"
    try:
        v = int(v)
    except (ValueError, TypeError):
        return str(v)
    return f"{v:,}"


def _render_pl_table(by_clinic_month: dict, clinic_short: str,
                     months: list[str]) -> None:
    """渲染 P&L 大表：rows=項目, cols=月份。"""
    # 收入面 rows
    rows: list[dict] = []

    def _row(label: str, getter, *, highlight: bool = False,
             section: str | None = None) -> None:
        row = {"項目": label}
        for sm in months:
            pl = by_clinic_month.get((clinic_short, sm))
            row[sm[:7]] = getter(pl) if pl else None
        rows.append(row | {"_highlight": highlight, "_section": section})

    # === 收入面 ===
    _row("A 健保收入(總點數) [資訊]", lambda p: p.nhi_points, section="收入")
    _row("A 暫付成數 % [資訊]",
         lambda p: f"{p.nhi_ratio_pct:.1f}%" if p.nhi_ratio_pct else "—")
    _row("A 點值 [資訊]",
         lambda p: f"{p.nhi_point_value:.4f}" if p.nhi_point_value else "—")
    _row("B 健保醫療給付", lambda p: p.b_nhi_paid)
    cash_label = (
        "C 現金總收入(含掛號費、整復)" if clinic_short == "澤豐"
        else "C 現金總收入(含掛號費)"
    )
    _row(cash_label, lambda p: p.c_cash_revenue)
    if clinic_short == "澤豐":
        _row("D 傳統整復推拿收入 [資訊,已含於C]", lambda p: p.d_massage)
        _row("E 澤沛金流匯入", lambda p: p.e_zepei_inflow)
    _row("F 其餘收入", lambda p: p.f_other_income)
    _row("G 總收入 = B+C+E+F", lambda p: p.g_total_income, highlight=True)

    # === 支出面 ===
    def _doctor_salary_cell(p):
        miss = getattr(p, "doctor_salary_missing", None) or []
        csc = getattr(p, "cash_salary_check", None)
        flags = ""
        if miss:
            flags += " ⚠️缺" + "、".join(m["doctor"] for m in miss)
        if csc and not csc.get("ok"):
            flags += " 🚨現金給薪異常"
        if flags:
            return f"{_fmt_amt(p.h_doctor)}{flags}"
        return p.h_doctor

    _row("H 醫師薪資", _doctor_salary_cell, section="支出")
    _row("H 護理師&助理薪資", lambda p: p.h_nurse)
    if clinic_short == "澤豐":
        _row("H 編制外人員薪資", lambda p: p.h_external)
    _row("H 薪資支出合計", lambda p: p.h_salary_total)
    _row("I 現金支出", lambda p: p.i_cash_expense)
    if clinic_short == "澤沛":
        _row("J 澤沛金流支出", lambda p: p.j_zepei_outflow)
    _row("K 合約支出", lambda p: p.k_contract)
    if clinic_short == "澤沛":
        _row("L 房租支出", lambda p: p.l_rent)
    _row("M 其餘支出", lambda p: p.m_other_expense)
    _row("N 總支出A(含合約)", lambda p: p.n_total_expense_a)
    _row("O 盈餘A = G - N", lambda p: p.o_profit_a, highlight=True)
    if clinic_short == "澤豐":
        _row("P 支票支出", lambda p: p.p_check)
        _row("Q 總支出B(含支票)", lambda p: p.q_total_expense_b)
        _row("R 盈餘B = G - Q", lambda p: p.r_profit_b, highlight=True)

    # 渲染：分離 highlight 標記欄位後做樣式
    df = pd.DataFrame([
        {k: v for k, v in r.items() if not k.startswith("_")} for r in rows
    ])
    highlight_idx = {i for i, r in enumerate(rows) if r.get("_highlight")}

    # 格式化金額欄
    month_cols = [sm[:7] for sm in months]
    for c in month_cols:
        df[c] = df[c].apply(
            lambda v: _fmt_amt(v) if not isinstance(v, str) else v
        )

    # 用 pandas Styler 做凸顯
    def _style_row(row):
        if row.name in highlight_idx:
            return [
                "background-color: #FFF8DC; font-weight: 700; color: #B8860B"
            ] * len(row)
        return [""] * len(row)

    styled = df.style.apply(_style_row, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)


def _render_pl_charts(by_clinic_month: dict, clinic_short: str,
                      chart_months: list[str]) -> None:
    """近 6 個月 G(總收入)/O(盈餘A) 柱狀圖；排除嚴重不完整月份。"""
    import altair as alt

    rows = []
    excluded: list[str] = []
    for sm in chart_months:
        pl = by_clinic_month.get((clinic_short, sm))
        if not pl:
            continue
        # 完整門檻：總收入 G > 0 且 薪資合計 H > 0
        # (任一為 0 即視為當月資料尚未齊全，例：剛上傳到 N+1 月薪資但 N 月健保未撥)
        if pl.g_total_income <= 0 or pl.h_salary_total <= 0:
            excluded.append(sm[:7])
            continue
        rows.append({
            "月份": sm[:7],
            "總收入 G": pl.g_total_income,
            "盈餘 O": pl.o_profit_a,
        })

    if not rows:
        st.info(f"{clinic_short}：近 6 個月內無資料完整的月份可比較")
        return

    df = pd.DataFrame(rows).sort_values("月份")

    col1, col2 = st.columns(2)
    with col1:
        chart_g = alt.Chart(df).mark_bar(color="#4A90E2", size=28).encode(
            x=alt.X("月份:N", sort="ascending", title=None),
            y=alt.Y("總收入 G:Q", title="總收入 G (NT$)"),
            tooltip=[
                "月份", alt.Tooltip("總收入 G:Q", format=","),
            ],
        ).properties(
            title=f"{clinic_short} 總收入 G 近 6 月",
            height=320,
        )
        st.altair_chart(chart_g, use_container_width=True)
    with col2:
        chart_o = alt.Chart(df).mark_bar(size=28).encode(
            x=alt.X("月份:N", sort="ascending", title=None),
            y=alt.Y("盈餘 O:Q", title="盈餘 O (NT$)"),
            color=alt.condition(
                "datum['盈餘 O'] >= 0",
                alt.value("#5CB85C"),
                alt.value("#D9534F"),
            ),
            tooltip=[
                "月份", alt.Tooltip("盈餘 O:Q", format=","),
            ],
        ).properties(
            title=f"{clinic_short} 盈餘 O 近 6 月（綠正紅負）",
            height=320,
        )
        st.altair_chart(chart_o, use_container_width=True)

    if excluded:
        st.caption(
            f"⚠️ 已排除資料不完整月份（G 或 H 為 0）：{', '.join(sorted(excluded))}"
        )


def _render_pl_breakdown(by_clinic_month: dict, clinic_short: str,
                         months: list[str]) -> None:
    """各月薪資/收入分項明細（debug 用，預設摺疊）。"""
    with st.expander(f"🔍 {clinic_short} 各月明細（驗證來源）"):
        # 月份 selector
        sel = st.selectbox(
            f"{clinic_short} 月份", months,
            format_func=lambda d: d[:7],
            key=f"breakdown_{clinic_short}",
        )
        pl = by_clinic_month.get((clinic_short, sel))
        if not pl:
            st.info("該月份無資料")
            return

        miss = getattr(pl, "doctor_salary_missing", None) or []
        if miss:
            lines = "；".join(
                f"{m['doctor']}（{m['reason']}"
                + (f"，系統實領 {m['expected']:,}" if m.get("expected") else "")
                + "）"
                for m in miss
            )
            st.warning(
                f"⚠️ H 醫師薪資缺漏（數據異常）：{lines}。"
                "該筆薪轉（若存在）暫列於護理師&助理薪資。"
            )

        csc = getattr(pl, "cash_salary_check", None)
        if csc:
            if csc.get("ok"):
                st.success(
                    f"✅ 現金給薪核對一致：手 KEY 扣除 {csc['keyed']:,} = "
                    f"系統計算領現合計 {csc['computed']:,}"
                )
            else:
                st.error(
                    f"🚨 現金給薪數據異常：手 KEY「現金給薪扣除」"
                    f"{csc['keyed']:,} ≠ 系統計算領現合計 {csc['computed']:,}"
                    f"（差 {csc['keyed'] - csc['computed']:+,}）。"
                    "請核對罐頭輸入金額與醫師薪資計算。"
                )

        sections = [
            ("H 醫師薪資", pl.doctor_salary_items),
            ("H 護理師&助理薪資", pl.nurse_salary_items),
            ("H 編制外人員薪資", pl.external_salary_items),
            ("B 健保醫療給付", pl.nhi_paid_items),
            ("C 現金總收入", pl.cash_revenue_items),
            ("D 傳統整復推拿", pl.massage_items),
            ("E 澤沛金流匯入", pl.zepei_inflow_items),
            ("F 其餘收入", pl.other_income_items),
            ("I 現金支出", pl.cash_expense_items),
            ("J 澤沛金流支出", pl.zepei_outflow_items),
            ("K 合約支出", pl.contract_items),
            ("L 房租支出", pl.rent_items),
            ("M 其餘支出", pl.other_expense_items),
            ("P 支票支出", pl.check_items),
        ]
        for title, items in sections:
            if not items:
                continue
            sub = pd.DataFrame(items)
            sub_sum = sum(int(it.get("amount") or 0) for it in items)
            st.markdown(f"**{title}** — 合計 NT$ {sub_sum:,}（{len(items)} 筆）")
            st.dataframe(sub, use_container_width=True, hide_index=True)


# ============================================================
# 2c. 財報列印
# ============================================================
def _open_in_new_tab_widget(html_str: str, key: str):
    """用 components.html 內嵌按鈕：點擊以 Blob 開新分頁顯示報表。"""
    import json
    import streamlit.components.v1 as components

    payload = json.dumps(html_str)
    btn_html = f"""
<div style="margin:4px 0;">
  <button id="open_{key}" style="background:#6A5ACD;color:#fff;border:none;
     padding:10px 18px;border-radius:8px;font-size:15px;cursor:pointer;
     font-weight:600;">🔗 開啟報表（新分頁）</button>
  <span id="hint_{key}" style="margin-left:10px;color:#b85c00;font-size:12px;"></span>
</div>
<script>
  const html_{key} = {payload};
  document.getElementById("open_{key}").onclick = function() {{
    try {{
      const blob = new Blob([html_{key}], {{type: "text/html;charset=utf-8"}});
      const url = URL.createObjectURL(blob);
      const w = window.open(url, "_blank");
      if (!w) {{
        document.getElementById("hint_{key}").innerText =
          "瀏覽器擋了彈出視窗，請改用下方「下載 HTML」按鈕。";
      }}
    }} catch (e) {{
      document.getElementById("hint_{key}").innerText = "開啟失敗：" + e;
    }}
  }};
</script>
"""
    components.html(btn_html, height=60)


def page_reports():
    st.title("🖨️ 財報列印")
    st.caption(
        "產生可列印成 A4（1~2 頁）的獨立 HTML 報表。點「開啟報表」會在新分頁顯示，"
        "於該分頁按 Ctrl+P 即可列印；亦可直接下載 HTML 檔。"
    )

    from datetime import datetime
    from data_processor import report_builder as rb
    from data_processor.monthly_pl import list_available_months as cf_months
    from data_processor.monthly_profit_loss import (
        list_available_months as pl_months,
    )

    sb = get_authed_client()

    REPORTS = {
        "A 月度實帳金流分析": ("cashflow", "month"),
        "B 季度實帳金流分析": ("cashflow", "quarter"),
        "C 年度實帳金流分析": ("cashflow", "year"),
        "D 月度損益分析": ("pl", "month"),
        "E 年度損益分析": ("pl", "year"),
    }

    c1, c2 = st.columns([3, 2])
    with c1:
        report_name = st.selectbox("報表種類", list(REPORTS.keys()),
                                   key="rpt_type")
    with c2:
        clinic = st.radio("診所", ["澤豐", "澤沛"], horizontal=True,
                          key="rpt_clinic")

    kind, gran = REPORTS[report_name]
    months = _filter_min_month(cf_months(sb) if kind == "cashflow"
                               else pl_months(sb))
    months = sorted(set(months), reverse=True)
    if not months:
        st.warning("⚠️ 尚無可用月份資料。")
        return

    # 期間選擇
    sel_month = sel_year = sel_q = None
    if gran == "month":
        sel_month = st.selectbox("月份", months,
                                 format_func=rb.roc_label, key="rpt_month")
    elif gran == "quarter":
        quarters = sorted(
            {rb.quarter_of(m) for m in months}, reverse=True)
        sel = st.selectbox(
            "季度", quarters,
            format_func=lambda yq: f"{yq[0]} 第 {yq[1]} 季", key="rpt_q")
        sel_year, sel_q = sel
    else:  # year
        years = sorted({int(m[:4]) for m in months}, reverse=True)
        sel_year = st.selectbox("年度", years,
                                format_func=lambda y: f"{y} 年", key="rpt_year")

    if not st.button("📄 產生報表", type="primary", key="rpt_gen"):
        return

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    with st.spinner("產生報表中..."):
        try:
            if kind == "cashflow" and gran == "month":
                html_str, err = rb.build_cashflow_monthly(
                    sb, clinic, sel_month, months, generated)
            elif kind == "cashflow" and gran == "quarter":
                html_str, err = rb.build_cashflow_quarterly(
                    sb, clinic, sel_year, sel_q, months, generated)
            elif kind == "cashflow" and gran == "year":
                html_str, err = rb.build_cashflow_yearly(
                    sb, clinic, sel_year, months, generated)
            elif kind == "pl" and gran == "month":
                html_str, err = rb.build_pl_monthly(
                    sb, clinic, sel_month, months, generated)
            else:  # pl year
                html_str, err = rb.build_pl_yearly(
                    sb, clinic, sel_year, months, generated)
        except Exception as e:
            st.error(f"產生報表失敗：{e}")
            return

    if err:
        st.warning(err, icon="⚠️")
        return

    st.success("✅ 報表已產生")
    fname = f"{clinic}_{report_name.split()[0]}_{report_name[2:]}.html"
    _open_in_new_tab_widget(html_str, key="rpt")
    st.download_button(
        "⬇️ 下載 HTML（備援）", data=html_str.encode("utf-8"),
        file_name=fname, mime="text/html", key="rpt_dl",
    )
    with st.expander("👁️ 內嵌預覽（外觀可能與列印略有差異）"):
        import streamlit.components.v1 as components
        components.html(html_str, height=700, scrolling=True)


# ============================================================
# 3. 本月資料匯入（Phase 2）
# ============================================================
def page_import():
    st.title("📥 本月資料匯入區")

    if not st.session_state.get("edit_mode", False):
        st.warning(
            "⚠️ 唯讀模式開啟中。如需上傳或修改資料，請在左下方勾選「啟用編輯模式」。"
        )
        return

    st.success("✅ 編輯模式啟用中")

    # ─── 玉山健保戶 CSV ───────────────────────────────
    _section_esun_health_csv()

    st.divider()

    # ─── 中信進出戶 CSV ───────────────────────────────
    _section_ctbc_csv()

    st.divider()

    # ─── 醫療費用付款通知書 HTML（批次）───────────────
    _section_nhi_notices()

    st.divider()

    # ─── 醫師自費統計（批次）─────────────────────────
    _section_cash_visits()

    st.divider()

    # ─── 健保人數+初診統計（批次）────────────────────
    _section_visit_count()

    st.divider()

    # ─── 門診申報金額統計報表 + A91+複針（批次）──────
    _section_outpatient_report()

    st.divider()

    # ─── 合理門診量（批次）──────────────────────────
    _section_capacity_quota()

    st.divider()

    # ─── 現金支出 ────────────────────────────────────
    _section_cash_expense()

    st.divider()

    # ─── 合約支出 ────────────────────────────────────
    _section_contract_expense()

    st.divider()

    # ─── 支票支出已併入「現金支出」自動分流（描述「支票-」開頭）───

    # ─── 調貨整理 ────────────────────────────────────
    _section_inventory_transfer()

    st.divider()

    # ─── 自費商品成本&售價 ─────────────────────────
    _section_self_pay_pricing()

    st.divider()

    # ─── 科中進貨價目表（金流結算用）──────────────
    _section_tcm_concentrate_pricing()

    st.divider()

    # ─── 員工薪資（自動偵測最新 sheet）─────────────
    _section_staff_salary()

    st.divider()

    # ─── 手 KEY 補充備註（金流註解 CRUD）───────────
    _section_manual_annotation()

    st.divider()

    # ─── 手 KEY 診所非常規收支（CRUD）──────────────
    _section_manual_entry()

    # ─── 其他類型（待實作）───────────────────────────
    st.divider()
    st.markdown("**🚧 其他資料來源（待實作）：**")
    st.markdown("""
    - 員工薪資表、@科中進貨價目表
    - 自費商品其他 sheets（自費藥粉、金流計算表）
    """)


def _ensure_esun_health_account(sb, clinic_short_name: str) -> int:
    """確保玉山健保戶 bank_account 存在，回傳 id（自動建立）"""
    clinic_resp = (
        sb.table("clinics")
        .select("id")
        .eq("short_name", clinic_short_name)
        .execute()
    )
    if not clinic_resp.data:
        raise ValueError(f"找不到診所 {clinic_short_name}")
    clinic_id = clinic_resp.data[0]["id"]

    acc_resp = (
        sb.table("bank_accounts")
        .select("id")
        .eq("clinic_id", clinic_id)
        .eq("bank", "玉山")
        .eq("account_type", "健保戶")
        .execute()
    )
    if acc_resp.data:
        return acc_resp.data[0]["id"]

    insert_resp = (
        sb.table("bank_accounts")
        .insert({
            "clinic_id": clinic_id,
            "bank": "玉山",
            "account_type": "健保戶",
            "account_no_mask": f"{clinic_short_name}-玉山-健保戶",
        })
        .execute()
    )
    return insert_resp.data[0]["id"]


def _section_esun_health_csv():
    """玉山健保戶 CSV 上傳區"""
    from data_processor.esun_csv import parse_esun_csv

    st.subheader("🏦 玉山健保戶 CSV")
    st.caption("健保署撥款入帳、員工薪資轉出、健保費代扣的對帳記錄")

    col1, col2 = st.columns([1, 3])
    with col1:
        clinic_choice = st.radio(
            "診所",
            ["澤豐", "澤沛"],
            key="esun_clinic_choice",
        )
    with col2:
        uploaded_file = st.file_uploader(
            f"上傳 {clinic_choice} 玉山健保戶 CSV",
            type=["csv"],
            key=f"esun_uploader_{clinic_choice}",
        )

    if uploaded_file is None:
        return

    try:
        sb = get_authed_client()
        account_id = _ensure_esun_health_account(sb, clinic_choice)
        records = parse_esun_csv(uploaded_file, account_id)
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return

    if not records:
        st.warning("CSV 沒有可匯入的交易記錄")
        return

    st.success(f"✅ 解析完成，共 {len(records)} 筆")

    preview_cols = [
        "transaction_date", "transaction_time", "summary",
        "amount", "balance", "memo_month", "counterparty",
    ]
    preview_df = pd.DataFrame(records)[preview_cols]
    st.dataframe(preview_df, use_container_width=True, height=300)

    if st.button(
        f"💾 確認匯入 {clinic_choice} 玉山健保戶（{len(records)} 筆）",
        type="primary",
        key=f"esun_import_{clinic_choice}",
    ):
        _import_bank_records(sb, records)


def _ensure_ctbc_account(sb, account_label: str) -> int:
    """
    確保中信進出戶 bank_account 存在，回傳 id

    account_label:
      "澤沛"     → clinic=澤沛, is_personal_mixed=False
      "澤豐&個人" → clinic=澤豐, is_personal_mixed=True
    """
    if account_label == "澤沛":
        clinic_short = "澤沛"
        is_mixed = False
        no_mask = "澤沛-中信-進出戶"
    elif account_label == "澤豐&個人":
        clinic_short = "澤豐"
        is_mixed = True
        no_mask = "澤豐-中信-進出戶（與院長個人混戶）"
    else:
        raise ValueError(f"未知帳戶 label：{account_label}")

    clinic_resp = (
        sb.table("clinics")
        .select("id")
        .eq("short_name", clinic_short)
        .execute()
    )
    if not clinic_resp.data:
        raise ValueError(f"找不到診所 {clinic_short}")
    clinic_id = clinic_resp.data[0]["id"]

    acc_resp = (
        sb.table("bank_accounts")
        .select("id")
        .eq("clinic_id", clinic_id)
        .eq("bank", "中信")
        .eq("account_type", "進出戶")
        .eq("is_personal_mixed", is_mixed)
        .execute()
    )
    if acc_resp.data:
        return acc_resp.data[0]["id"]

    insert_resp = (
        sb.table("bank_accounts")
        .insert({
            "clinic_id": clinic_id,
            "bank": "中信",
            "account_type": "進出戶",
            "account_no_mask": no_mask,
            "is_personal_mixed": is_mixed,
        })
        .execute()
    )
    return insert_resp.data[0]["id"]


def _section_ctbc_csv():
    """中信進出戶 CSV 上傳區（取代加密 PDF）"""
    from data_processor.ctbc_csv import parse_ctbc_csv

    st.subheader("🏦 中信進出戶 CSV")
    st.caption("中信網銀「活存明細查詢」下載的 CSV（不需密碼，比 PDF 更可靠）")

    col1, col2 = st.columns([1, 3])
    with col1:
        account_choice = st.radio(
            "帳戶",
            ["澤沛", "澤豐&個人"],
            key="ctbc_account_choice",
            help="澤豐&個人 是與周院長個人混用的中信戶",
        )
    with col2:
        uploaded_file = st.file_uploader(
            f"上傳 {account_choice} 中信 CSV",
            type=["csv"],
            key=f"ctbc_uploader_{account_choice}",
        )

    if uploaded_file is None:
        return

    try:
        sb = get_authed_client()
        account_id = _ensure_ctbc_account(sb, account_choice)
        records = parse_ctbc_csv(uploaded_file, account_id)
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return

    if not records:
        st.warning("CSV 沒有可匯入的交易記錄")
        return

    st.success(f"✅ 解析完成，共 {len(records)} 筆")

    preview_cols = [
        "transaction_date", "summary", "amount", "balance",
        "channel", "counterparty", "note",
    ]
    preview_df = pd.DataFrame(records)[preview_cols]
    st.dataframe(preview_df, use_container_width=True, height=300)

    if st.button(
        f"💾 確認匯入 {account_choice} 中信進出戶（{len(records)} 筆）",
        type="primary",
        key=f"ctbc_import_{account_choice}",
    ):
        _import_bank_records(sb, records)


def _section_nhi_notices():
    """醫療費用付款通知書 HTML 批次上傳區（Sprint 2.3）"""
    from data_processor.nhi_notice_html import (
        parse_filename,
        parse_nhi_notice_html,
    )

    st.subheader("📄 醫療費用付款通知書 HTML（批次）")
    st.caption(
        "健保署系統下載的 HTML（Big5 編碼）。可一次選多份；機構由檔名自動識別，"
        "重複檔名會跳過。"
    )

    uploaded_files = st.file_uploader(
        "上傳一份或多份 HTML",
        type=["html", "htm"],
        accept_multiple_files=True,
        key="nhi_uploader",
    )
    if not uploaded_files:
        return

    sb = get_authed_client()

    clinics_resp = sb.table("clinics").select("id, code, short_name").execute()
    code_to_id = {c["code"]: c["id"] for c in clinics_resp.data}
    id_to_short = {c["id"]: c["short_name"] for c in clinics_resp.data}

    records: list[dict] = []
    errors: list[str] = []
    for f in uploaded_files:
        try:
            meta = parse_filename(f.name)
            clinic_id = code_to_id.get(meta["inst_code"])
            if clinic_id is None:
                raise ValueError(
                    f"檔名機構碼 {meta['inst_code']} 不在 clinics 表"
                )
            rec = parse_nhi_notice_html(f, f.name, clinic_id)
            records.append(rec)
        except Exception as e:
            errors.append(f"{f.name}：{e}")

    if errors:
        st.error("部分檔案解析失敗：")
        for err in errors:
            st.code(err)

    if not records:
        return

    st.success(f"✅ 解析成功 {len(records)} 份")

    preview = pd.DataFrame(records).copy()
    preview["診所"] = preview["clinic_id"].map(id_to_short)
    preview_cols = [
        "source_filename", "診所", "service_month",
        "apply_date", "payment_date",
        "applied_amount", "interim_ratio_pct", "point_value",
        "paid_amount", "deduction_amount", "payment_type",
    ]
    st.dataframe(
        preview[preview_cols], use_container_width=True, height=300
    )

    # 同 (clinic, service_month) 聚合預覽
    agg = (
        preview.groupby(["診所", "service_month"], as_index=False)
        .agg(份數=("source_filename", "count"), 合計實付=("paid_amount", "sum"))
    )
    st.markdown("**按 (診所, 服務月份) 聚合：**")
    st.dataframe(agg, use_container_width=True, hide_index=True)

    if st.button(
        f"💾 確認匯入 {len(records)} 份健保通知書",
        type="primary",
        key="nhi_import_btn",
    ):
        _import_nhi_records(sb, records)


def _import_nhi_records(sb, records: list[dict]):
    """寫入 nhi_payment_notices（依 source_filename UNIQUE 防重複）"""
    inserted = 0
    skipped = 0
    errors = []
    progress = st.progress(0, text="匯入中...")
    total = len(records)

    BATCH_SIZE = 20
    for i in range(0, total, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        try:
            # ignore_duplicates=False → 同檔名重上傳會覆蓋舊值
            # （健保通知書內容可能改版含扣款，需要更新）
            resp = (
                sb.table("nhi_payment_notices")
                .upsert(batch, on_conflict="source_filename")
                .execute()
            )
            inserted += len(resp.data) if resp.data else 0
        except Exception as e:
            errors.append(f"批次 {i}-{i+len(batch)}：{e}")
        progress.progress(min((i + BATCH_SIZE) / total, 1.0))

    progress.empty()

    if errors:
        st.error("部分匯入失敗：")
        for err in errors:
            st.code(err)
    if inserted:
        st.success(f"✅ 新增 {inserted} 份")
    if skipped:
        st.info(f"ℹ️ 跳過重複 {skipped} 份（依 source_filename）")
    if inserted and not errors:
        st.balloons()


def _section_cash_visits():
    """醫師自費統計批次上傳區（Sprint 2.6）— 薪資抽成輸入"""
    from data_processor.cash_visits import (
        parse_cash_visits,
        parse_filename as parse_cash_filename,
        clinic_hint_from_filename,
    )

    st.subheader("💰 醫師自費統計（批次）")
    st.caption(
        "薪資抽成輸入。檔內姓名/地址/電話不會寫入 DB（隱私）。"
        "可一次選多份不同醫師的檔案；診所請手動選定，醫師由檔名自動識別。"
    )

    col1, col2 = st.columns([1, 3])
    with col1:
        clinic_choice = st.radio(
            "診所",
            ["澤豐", "澤沛"],
            key="cash_clinic_choice",
        )
    with col2:
        uploaded_files = st.file_uploader(
            f"上傳 {clinic_choice} 醫師自費統計（多份 xlsx/xls）",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            key=f"cash_uploader_{clinic_choice}",
        )
    if not uploaded_files:
        return

    sb = get_authed_client()

    clinic_resp = (
        sb.table("clinics").select("id, short_name").eq("short_name", clinic_choice).execute()
    )
    if not clinic_resp.data:
        st.error(f"找不到診所 {clinic_choice}")
        return
    clinic_id = clinic_resp.data[0]["id"]

    doctors_resp = sb.table("doctors").select("id, name").execute()
    name_to_did = {d["name"]: d["id"] for d in doctors_resp.data}

    all_records: list[dict] = []
    summaries: list[dict] = []
    errors: list[str] = []

    mismatches: list[str] = []  # 檔名推測診所與選擇不一致的清單

    for f in uploaded_files:
        try:
            meta = parse_cash_filename(f.name)
            doctor = meta["doctor"]
            doctor_id = name_to_did.get(doctor)
            if doctor_id is None:
                raise ValueError(f"醫師 {doctor} 不在 doctors 表")
            recs, totals = parse_cash_visits(
                f, f.name, clinic_id, doctor_id,
                expected_doctor_name=doctor,
            )
            all_records.extend(recs)
            hint = clinic_hint_from_filename(f.name)
            if hint and hint != clinic_choice:
                mismatch_flag = "❌ 不一致"
                mismatches.append(f"{f.name}：檔名像是「{hint}」")
            elif hint == clinic_choice:
                mismatch_flag = "✅"
            else:
                mismatch_flag = "❓ 無法推測"
            summaries.append({
                "檔名": f.name,
                "推測診所": hint or "（無法推測）",
                "你選": clinic_choice,
                "對診所": mismatch_flag,
                "醫師": doctor,
                "服務月": meta["service_month"],
                "筆數": totals["parsed_count"],
                "含掛號合計": totals["parsed_total_raw"],
                "不含掛號合計": totals["parsed_total_excl_reg"],
                "檔案總計": totals["file_total"],
                "對帳": "✅" if totals["matches"] else "❌",
                "對帳模式": totals["registration_handling"],
            })
        except Exception as e:
            errors.append(f"{f.name}：{e}")

    if errors:
        st.error("部分檔案解析失敗：")
        for e in errors:
            st.code(e)

    if not summaries:
        return

    st.markdown("**檔案彙整：**")
    st.dataframe(pd.DataFrame(summaries), use_container_width=True, hide_index=True)

    bad = [s for s in summaries if s["對帳"] != "✅"]
    if bad:
        st.warning(
            f"⚠️ 有 {len(bad)} 份檔案的合計與檔案總計列對不上，"
            "請檢查後再決定是否匯入"
        )

    # 診所不一致警告 + 強制確認 checkbox
    confirm_mismatch = True
    if mismatches:
        st.error(
            f"❌ **{len(mismatches)} 份檔名推測不屬於「{clinic_choice}」**：\n\n"
            + "\n".join(f"- {m}" for m in mismatches)
            + "\n\n"
            f"**強烈建議**：取消勾選後，回上面把「診所」radio 改成正確的，再重選檔案。"
        )
        confirm_mismatch = st.checkbox(
            "我確認真的要把這些檔案寫入「" + clinic_choice + "」（跨診所支援等特殊情境）",
            key=f"cash_confirm_mismatch_{clinic_choice}",
        )

    st.markdown(f"**全部資料筆數：{len(all_records)} 筆**（不含姓名/地址/電話）")
    if all_records:
        # 預覽前 10 筆（去敏感欄）
        preview_cols = [
            "visit_date", "chart_no", "diagnosis", "prescription",
            "registration", "internal_drug", "external_drug", "acupuncture",
            "trauma", "dislocation", "wellness", "herb_decoction",
            "consult", "lab", "other", "cash_total",
        ]
        preview = pd.DataFrame(all_records)[preview_cols]
        st.dataframe(preview.head(10), use_container_width=True)

    if st.button(
        f"💾 確認匯入 {clinic_choice} 自費統計（{len(all_records)} 筆）",
        type="primary",
        key=f"cash_import_btn_{clinic_choice}",
        disabled=not confirm_mismatch,
    ):
        _import_cash_records(sb, all_records)


def _section_cash_expense():
    """現金支出 + 自動分流支票（描述「支票-」開頭歸 check_expense）"""
    from data_processor.expenses import parse_cash_expense_split

    st.subheader("💵 現金支出（含支票自動分流）")
    st.caption(
        "檔名範例：『澤豐/澤沛中醫診所現金支出.xlsx』。"
        "檔內描述以「支票-XXX(銀行)」開頭的列會自動分流到 check_expense 表，"
        "其他歸 cash_expense。**不需另外上傳支票檔。**"
    )

    st.info(
        "ℹ️ **澤豐**：現金支出直接入實帳（x3，本月）。\n\n"
        "ℹ️ **澤沛**：現金支出「合計」仍由系統從澤沛中信 x5 結算交易自動入帳"
        "（標籤如「沛02月現金支出」）；本區上傳的澤沛檔僅提供**逐筆明細對帳**"
        "（顯示於月度實帳金流分析的「x5 澤沛現金結算明細」），不會重複計入合計。"
    )

    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        clinic_choice = st.radio("診所", ["澤豐", "澤沛"], key="cash_exp_clinic")
    with col2:
        roc_year = st.number_input(
            "民國年", min_value=110, max_value=130, value=115, step=1,
            key="cash_exp_year",
        )
    with col3:
        uploaded = st.file_uploader(
            f"上傳 {clinic_choice} 現金支出 xlsx",
            type=["xlsx"],
            key=f"cash_exp_uploader_{clinic_choice}",
        )
    if not uploaded:
        return

    sb = get_authed_client()
    clinic_resp = (
        sb.table("clinics").select("id").eq("short_name", clinic_choice).execute()
    )
    if not clinic_resp.data:
        st.error(f"找不到診所 {clinic_choice}")
        return
    clinic_id = clinic_resp.data[0]["id"]

    try:
        cash_records, check_records = parse_cash_expense_split(
            uploaded, uploaded.name, clinic_id, roc_year=int(roc_year)
        )
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return

    if not (cash_records or check_records):
        st.warning("無可匯入的資料")
        return

    st.success(
        f"✅ 解析完成：現金 {len(cash_records)} 筆 + 支票 {len(check_records)} 筆"
    )

    if cash_records:
        df = pd.DataFrame(cash_records)
        df_sum = df.copy()
        df_sum["月份"] = df_sum["expense_date"].str[:7]
        summary = df_sum.groupby("月份", as_index=False).agg(
            筆數=("amount", "count"), 合計=("amount", "sum"),
        )
        st.markdown("**現金支出按月份彙總：**")
        st.dataframe(summary, use_container_width=True, hide_index=True)
        with st.expander("逐筆現金支出預覽"):
            st.dataframe(
                df[["expense_date", "description", "amount", "note"]],
                use_container_width=True, height=300, hide_index=True,
            )

    if check_records:
        df_chk = pd.DataFrame(check_records)
        df_chk_sum = df_chk.copy()
        df_chk_sum["月份"] = df_chk_sum["issue_month"].str[:7]
        chk_summary = df_chk_sum.groupby("月份", as_index=False).agg(
            筆數=("amount", "count"), 合計=("amount", "sum"),
        )
        st.markdown("**支票分流按月份彙總：**")
        st.dataframe(chk_summary, use_container_width=True, hide_index=True)
        with st.expander("逐筆支票預覽"):
            st.dataframe(
                df_chk[["issue_month", "vendor", "amount", "bank", "note"]],
                use_container_width=True, height=300, hide_index=True,
            )

    # 預先算出本檔涵蓋的月份範圍
    cover_months = sorted({r["accrual_month"] for r in cash_records}) if cash_records else []
    cover_chk_months = sorted({r["issue_month"] for r in check_records}) if check_records else []
    if cover_months:
        st.warning(
            f"⚠️ 確認匯入會**整月覆蓋** {clinic_choice} 診所 cash_expense："
            f"{cover_months[0][:7]} ~ {cover_months[-1][:7]}（共 {len(cover_months)} 個月）。"
            f"原資料會先全部刪除再寫入，確保描述異動 / 支票分流變更都能完整反映。"
        )

    if st.button(
        f"💾 確認匯入（現金 {len(cash_records)} + 支票 {len(check_records)}）",
        type="primary",
        key=f"cash_exp_save_{clinic_choice}",
    ):
        try:
            # ─── cash_expense 整月覆蓋：DELETE 涵蓋月份 → INSERT 新 records ───
            cleaned_cash = 0
            if cover_months:
                del_resp = (
                    sb.table("cash_expense").delete()
                    .eq("clinic_id", clinic_id)
                    .gte("accrual_month", cover_months[0])
                    .lte("accrual_month", cover_months[-1])
                    .execute()
                )
                cleaned_cash = len(del_resp.data or [])
            if cash_records:
                sb.table("cash_expense").insert(cash_records).execute()

            # ─── check_expense 兩家共用：清同月份範圍但不在新 records 中的舊 ───
            cleaned_chk = 0
            if cover_chk_months:
                new_keys = {(r["issue_month"], r["vendor"], r["bank"]) for r in check_records}
                old_chk = (
                    sb.table("check_expense")
                    .select("id, issue_month, vendor, bank")
                    .gte("issue_month", cover_chk_months[0])
                    .lte("issue_month", cover_chk_months[-1])
                    .execute().data
                )
                stale_chk_ids = [
                    r["id"] for r in old_chk
                    if (r["issue_month"], r["vendor"], r["bank"]) not in new_keys
                ]
                if stale_chk_ids:
                    sb.table("check_expense").delete().in_("id", stale_chk_ids).execute()
                    cleaned_chk = len(stale_chk_ids)
            if check_records:
                sb.table("check_expense").upsert(
                    check_records, on_conflict="issue_month,vendor,bank",
                ).execute()

            msg = f"✅ 寫入：現金 {len(cash_records)} 筆、支票 {len(check_records)} 筆"
            if cleaned_cash:
                msg += f"（清掉舊現金 {cleaned_cash} 筆）"
            if cleaned_chk:
                msg += f"（清掉舊支票 {cleaned_chk} 筆）"
            st.success(msg)
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")
    # 注意：支票（描述開頭「支票-」）已分流到 check_expense
    # _section_cash_expense 只處理一般現金支出。
    # 如需重新處理（含支票分流），請改用 _section_cash_expense_v2


def _section_contract_expense():
    """合約支出（Sprint 2.7a）— 橫向月度表自動轉長表"""
    from data_processor.expenses import parse_contract_expense

    st.subheader("📜 合約支出（年度檔，橫向月度表）")
    st.caption(
        "檔名範例：『澤豐/澤沛中醫診所合約支出.xlsx』。系統把橫表轉成"
        "(月份 × 廠商) 的長表逐筆寫入 contract_expense。"
    )

    st.info(
        "ℹ️ **澤沛合約支出由系統從澤沛中信交易自動辨識**（標籤如「沛02月合約」），"
        "不需上傳檔案。本區僅供澤豐使用。"
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        clinic_choice = st.radio("診所", ["澤豐"], key="contract_exp_clinic")
    with col2:
        uploaded = st.file_uploader(
            f"上傳 {clinic_choice} 合約支出 xlsx",
            type=["xlsx"],
            key=f"contract_exp_uploader_{clinic_choice}",
        )
    if not uploaded:
        return

    sb = get_authed_client()
    clinic_resp = (
        sb.table("clinics").select("id").eq("short_name", clinic_choice).execute()
    )
    if not clinic_resp.data:
        st.error(f"找不到診所 {clinic_choice}")
        return
    clinic_id = clinic_resp.data[0]["id"]

    try:
        records = parse_contract_expense(uploaded, uploaded.name, clinic_id)
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return

    if not records:
        st.warning("無可匯入的資料")
        return

    df = pd.DataFrame(records)
    st.success(f"✅ 解析 {len(records)} 筆")

    # 月份彙總
    summary = df.groupby("service_month", as_index=False).agg(
        筆數=("amount", "count"), 合計=("amount", "sum"),
    )
    st.markdown("**按月份彙總：**")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    # 廠商彙總
    by_vendor = df.groupby("vendor", as_index=False).agg(
        筆數=("amount", "count"), 合計=("amount", "sum"),
    ).sort_values("合計", ascending=False)
    st.markdown("**按廠商彙總：**")
    st.dataframe(by_vendor, use_container_width=True, hide_index=True)

    st.markdown("**逐筆預覽：**")
    st.dataframe(
        df[["service_month", "vendor", "amount"]],
        use_container_width=True, height=300, hide_index=True,
    )

    if st.button(
        f"💾 確認匯入 {clinic_choice} 合約支出（{len(records)} 筆）",
        type="primary",
        key=f"contract_exp_save_{clinic_choice}",
    ):
        try:
            sb.table("contract_expense").upsert(
                records, on_conflict="clinic_id,service_month,vendor",
            ).execute()
            st.success(f"✅ 寫入 {len(records)} 筆")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _section_self_pay_pricing():
    """
    自費商品成本&售價 — 全表 single source of truth

    上傳邏輯：DELETE 全表 + INSERT 全部新資料
    每次上傳完全覆蓋舊資料；effective_month 用今天當 placeholder 不顯示給使用者。
    """
    from data_processor.pricing import parse_self_pay_all_sheets

    st.subheader("🛒 自費商品成本&售價（最新版本，全表覆蓋）")
    st.caption(
        "🔄 **每次上傳會完全覆蓋舊資料**。檔案是 single source of truth，"
        "沒有月份版本概念；上傳即更新。"
        "解析兩個 sheet：「膠囊&OTC」+「自費藥粉&自費商品」。"
    )

    sb = get_authed_client()

    # 顯示目前 DB 狀態（用 count=exact 取得真實筆數，不受 1000 上限影響）
    try:
        resp = (
            sb.table("product_pricing").select("id", count="exact")
            .limit(1).execute()
        )
        existing_count = resp.count if resp.count is not None else 0
        if existing_count:
            st.info(f"📋 目前 DB 有 **{existing_count}** 筆資料")
        else:
            st.info("📋 目前 DB 為空")
    except Exception as e:
        st.warning(f"讀取 DB 狀態失敗：{e}")

    uploaded = st.file_uploader(
        "上傳新版「自費商品成本&售價」xlsx（取代既有資料）",
        type=["xlsx"],
        key="pricing_uploader",
    )

    # effective_month placeholder = 今天的月份首日（schema NOT NULL 但邏輯不再用此欄區分版本）
    from datetime import date
    effective_month = date.today().replace(day=1).isoformat()

    if not uploaded:
        return

    try:
        records = parse_self_pay_all_sheets(uploaded, uploaded.name, effective_month)
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return
    if not records:
        st.warning("無可匯入的資料")
        return

    df = pd.DataFrame(records)
    st.success(f"✅ 解析 {len(records)} 筆（合併兩個 sheet，重複品項 OTC 優先）")

    by_vendor = df.groupby("vendor", as_index=False).agg(
        筆數=("product_name", "count"),
        平均進價=("cost_price", "mean"),
        平均售價=("sale_price", "mean"),
    )
    st.markdown("**按廠商彙總：**")
    st.dataframe(by_vendor, use_container_width=True, hide_index=True)

    st.markdown("**逐筆預覽：**")
    st.dataframe(
        df[["vendor", "product_name", "unit", "cost_price", "sale_price", "note"]],
        use_container_width=True, height=300, hide_index=True,
    )

    st.warning(
        "⚠️ 確認匯入會 **DELETE 整張 product_pricing 表 + 重新 INSERT**。"
        "舊資料不可復原，請確認新版資料已備齊（沒漏掉的廠商品項）再按下。"
    )

    if st.button(
        f"💾 確認覆蓋全表（{len(records)} 筆）",
        type="primary",
        key="pricing_save_btn",
    ):
        try:
            # 1. DELETE 全表
            # Supabase Python SDK 沒有 truncate；用 .delete().neq("id", -1)
            sb.table("product_pricing").delete().neq("id", -1).execute()
            # 2. INSERT 新資料
            sb.table("product_pricing").insert(records).execute()
            st.success(f"✅ 已清空舊資料並寫入 {len(records)} 筆")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _section_tcm_concentrate_pricing():
    """
    科中進貨價目表 — 全表 single source of truth（澤豐澤沛金流結算用）

    上傳邏輯：DELETE 全表 + INSERT 全部新資料
    僅解析 sheet「科學中藥-複」+「科學中藥-單方」；廠商白名單：天一/港香蘭/莊松榮/科達/順天堂/仙豐
    """
    from data_processor.tcm_concentrate import parse_tcm_concentrate

    st.subheader("🧪 科中進貨價目表（金流結算用，全表覆蓋）")
    st.caption(
        "🔄 **每次上傳會完全覆蓋舊資料**。僅解析「科學中藥-複」+「科學中藥-單方」"
        "兩個 sheet。廠商白名單：天一/港香蘭/莊松榮/科達/順天堂/仙豐"
        "（順天堂K裝、莊無 不參與計算）。供「澤豐澤沛金流結算」頁面試算用，"
        "不影響月度 P&L 認列。"
    )

    sb = get_authed_client()

    try:
        # 用 count=exact 取得真實筆數（避免被 1000 列預設上限誤導）
        resp = (
            sb.table("tcm_concentrate_pricing").select("id", count="exact")
            .limit(1).execute()
        )
        existing_count = resp.count if resp.count is not None else 0
        if existing_count:
            cat_resp = (
                sb.table("tcm_concentrate_pricing")
                .select("category", count="exact")
                .eq("category", "複方").limit(1).execute()
            )
            fang_count = cat_resp.count if cat_resp.count is not None else 0
            single_count = existing_count - fang_count
            st.info(
                f"📋 目前 DB 有 **{existing_count}** 筆 "
                f"(複方 {fang_count} / 單方 {single_count})"
            )
        else:
            st.info("📋 目前 DB 為空")
    except Exception as e:
        st.warning(
            f"讀取 DB 狀態失敗：{e}（請確認 tcm_concentrate_pricing 表已建立，"
            "schema 見頁面下方說明）"
        )

    uploaded = st.file_uploader(
        "上傳新版「@科中進貨價目表.xlsx」（取代既有資料）",
        type=["xlsx"],
        key="tcm_pricing_uploader",
    )

    from datetime import date
    effective_month = date.today().replace(day=1).isoformat()

    if not uploaded:
        with st.expander("📐 首次使用：建表 SQL"):
            st.code(
                "CREATE TABLE IF NOT EXISTS tcm_concentrate_pricing (\n"
                "  id SERIAL PRIMARY KEY,\n"
                "  category TEXT NOT NULL CHECK (category IN ('複方','單方')),\n"
                "  product_name TEXT NOT NULL,\n"
                "  vendor TEXT NOT NULL,\n"
                "  price NUMERIC(10,2) NOT NULL,\n"
                "  effective_month DATE,\n"
                "  source_filename TEXT,\n"
                "  uploaded_at TIMESTAMPTZ DEFAULT NOW(),\n"
                "  UNIQUE (category, product_name, vendor)\n"
                ");",
                language="sql",
            )
        return

    try:
        records = parse_tcm_concentrate(uploaded, uploaded.name, effective_month)
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return
    if not records:
        st.warning("無可匯入的資料（請檢查 sheet 名稱是否為「科學中藥-複」「科學中藥-單方」）")
        return

    df = pd.DataFrame(records)
    st.success(f"✅ 解析 {len(records)} 筆")

    by_cv = df.groupby(["category", "vendor"], as_index=False).size()
    by_cv.columns = ["category", "廠商", "筆數"]
    st.markdown("**按 (類別, 廠商) 彙總：**")
    st.dataframe(by_cv, use_container_width=True, hide_index=True)

    st.markdown("**逐筆預覽（前 200 筆）：**")
    st.dataframe(
        df[["category", "product_name", "vendor", "price"]].head(200),
        use_container_width=True, height=300, hide_index=True,
    )

    st.warning(
        "⚠️ 確認匯入會 **DELETE 整張 tcm_concentrate_pricing 表 + 重新 INSERT**。"
    )

    if st.button(
        f"💾 確認覆蓋全表（{len(records)} 筆）",
        type="primary", key="tcm_pricing_save_btn",
    ):
        try:
            sb.table("tcm_concentrate_pricing").delete().neq("id", -1).execute()
            sb.table("tcm_concentrate_pricing").insert(records).execute()
            st.success(f"✅ 已清空舊資料並寫入 {len(records)} 筆")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _section_staff_salary():
    """員工薪資批次匯入（Sprint 2.8c）— 支援單月、選定月、全部月份"""
    from data_processor.staff_salary import parse_staff_salary, list_sheets

    st.subheader("👤 員工薪資（多月 sheet）")
    st.caption(
        "一個檔多個月 sheet。可選擇：自動最新 / 指定月份 / 全部月份一次匯入。"
        "同月份「-更正」優先採用。抓員工總額 + 跨診所代付（影響豐沛金流）。"
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        default_clinic = st.radio(
            "檔案主聘診所",
            ["澤豐", "澤沛"],
            key="staff_clinic",
            help="一般員工區塊（無代付字樣）會歸到此診所",
        )
    with col2:
        uploaded = st.file_uploader(
            "上傳薪資 xlsx", type=["xlsx"], key="staff_uploader"
        )

    if not uploaded:
        return

    sb = get_authed_client()
    clinics_resp = sb.table("clinics").select("id, short_name").execute()
    short_to_cid = {c["short_name"]: c["id"] for c in clinics_resp.data}
    cid_to_short = {v: k for k, v in short_to_cid.items()}
    default_cid = short_to_cid[default_clinic]

    # 列出檔內所有月份 sheet
    try:
        sheets = list_sheets(uploaded)
    except Exception as e:
        st.error(f"讀取檔內 sheet 失敗：{e}")
        return

    if not sheets:
        st.error("檔案內找不到「薪資條XXX年XX月」格式的 sheet")
        return

    sheet_options = ["（自動最新）"] + [f"{sn}（{sm[:7]}）" for sn, sm in sheets]
    st.markdown(f"**檔內共 {len(sheets)} 個月份 sheet** — 最新：{sheets[0][1][:7]}")
    chosen = st.selectbox(
        "解析範圍",
        options=sheet_options,
        key="staff_sheet_pick",
        help="選「自動最新」= 只解析最新月份；選某 sheet = 只解析該月份",
    )

    if chosen == "（自動最新）":
        target_sheet = None
    else:
        target_sheet = chosen.split("（")[0]

    try:
        sheet_name, records = parse_staff_salary(
            uploaded, uploaded.name, default_cid, short_to_cid,
            target_sheet=target_sheet,
        )
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return

    st.success(
        f"📋 已解析 sheet：**{sheet_name}**　|　**{len(records)}** 位員工"
    )

    if records:
        df = pd.DataFrame(records)
        df["主聘診所"] = df["clinic_id"].map(cid_to_short)
        df["實付方"] = df["paid_by_clinic_id"].map(cid_to_short).fillna("（自付）")
        cols = [
            "service_month", "主聘診所", "employee_label",
            "gross_salary", "實付方", "note",
        ]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)

    cross = [r for r in records if r["paid_by_clinic_id"]]
    if cross:
        st.markdown("**🔁 跨診所代付摘要（影響豐沛金流）：**")
        from collections import defaultdict
        agg = defaultdict(int)
        for r in cross:
            owner = cid_to_short.get(r["clinic_id"], "?")
            payer = cid_to_short.get(r["paid_by_clinic_id"], "?")
            agg[(owner, payer)] += r["gross_salary"]
        for (owner, payer), total in agg.items():
            st.markdown(
                f"- **{owner}** 應付薪資但 **{payer}** 代付 → "
                f"{owner} 應給 {payer} **NT {total:,} 元**"
            )

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        if st.button(
            f"💾 匯入此 sheet（{len(records)} 筆）",
            type="primary",
            key="staff_save",
            disabled=not records,
        ):
            try:
                sb.table("staff_salary_summary").upsert(
                    records,
                    on_conflict="clinic_id,service_month,employee_label",
                ).execute()
                st.success(f"✅ 寫入 {len(records)} 筆")
                st.balloons()
            except Exception as e:
                st.error(f"寫入失敗：{e}")
    with btn_col2:
        if st.button(
            f"📚 全部 {len(sheets)} 個月份一次匯入",
            key="staff_save_all",
            help="逐月解析所有 sheet 並 upsert。同 (clinic, service_month, employee) 會覆蓋。",
        ):
            total_written = 0
            errors: list[str] = []
            for sn, _sm in sheets:
                try:
                    _, recs = parse_staff_salary(
                        uploaded, uploaded.name, default_cid, short_to_cid,
                        target_sheet=sn,
                    )
                    if recs:
                        sb.table("staff_salary_summary").upsert(
                            recs,
                            on_conflict="clinic_id,service_month,employee_label",
                        ).execute()
                        total_written += len(recs)
                except Exception as e:
                    errors.append(f"{sn}: {e}")
            if errors:
                st.warning(f"部分 sheet 失敗：\n" + "\n".join(errors))
            st.success(f"✅ 全部月份共寫入 {total_written} 筆")
            st.balloons()


_ANN_CATEGORY_LABELS = {
    None: "金流備註",
    "memo_only": "🟡 只記帳（不影響金流，只進月度損益分析）",
    "capital_injection": "🔴 股東注資（排除於月度損益分析收入）",
    "director_personal": "🔴 院長私人帳務（排除於實帳金流與損益的收入與支出）",
}
_ANN_CATEGORY_OPTIONS = list(_ANN_CATEGORY_LABELS.values())
_ANN_CATEGORY_BY_LABEL = {v: k for k, v in _ANN_CATEGORY_LABELS.items()}


def _ann_quick_templates(sb, rows: list[dict], short_to_cid: dict):
    """金流補充備註 — 每月罐頭快速輸入（選月份＋填數字即存檔）。

    3 個模板：
      1. 傳統整復推拿收入 — 澤豐/中信/存現/只記帳（memo_only）
      2. 澤豐現金收入 — 金流備註 + 現金給薪扣除（v13）
      3. 澤沛現金收入 — 金流備註 + 現金給薪扣除（v13）
    現金收入模板：amount=實際存入(收入−給薪扣除) 供銀行對帳；
    gross_amount/cash_salary_deduction 供實帳與損益記帳。
    entry_date=收入次月 1 日（現金慣例次月初存入，配對走同月金額比對）。
    同月重存 = 覆蓋更新既有那筆。
    """
    from datetime import date as _date

    st.markdown("**⚡ 每月罐頭快速輸入**")

    # 月份選項：近 14 個「收入月份」（預設上個月）
    today = _date.today()
    cur = today.replace(day=1)
    months: list[str] = []
    for _ in range(14):
        months.append(cur.isoformat())
        cur = (cur - pd.Timedelta(days=1)).replace(day=1)

    def _fmt_m(iso: str) -> str:
        y, m = int(iso[:4]), int(iso[5:7])
        return f"{y - 1911}年{m}月（{iso[:7]}）"

    def _next_month_first(iso: str) -> str:
        d = _date.fromisoformat(iso)
        return (_date(d.year + 1, 1, 1) if d.month == 12
                else _date(d.year, d.month + 1, 1)).isoformat()

    def _save(payload: dict, desc_prefix: str, ok_msg: str):
        """依 description 前綴找同月既有列 → update；否則 insert。"""
        exist = next(
            (r for r in rows
             if (r.get("description") or "").startswith(desc_prefix)),
            None,
        )
        try:
            if exist:
                sb.table("manual_annotation").update(payload).eq(
                    "id", exist["id"]).execute()
            else:
                sb.table("manual_annotation").insert(payload).execute()
            st.session_state["_ann_just_saved"] = True
            st.rerun()
        except Exception as e:
            if "gross_amount" in str(e) or "cash_salary_deduction" in str(e):
                st.error(
                    "儲存失敗：資料庫缺 v13 欄位。"
                    "請先到 Supabase SQL Editor 執行 migration_v12_to_v13.sql。"
                )
            else:
                st.error(f"儲存失敗：{e}")

    tab_ma, tab_fz, tab_fp = st.tabs(
        ["🙌 傳統整復推拿收入", "💰 澤豐現金收入", "💰 澤沛現金收入"]
    )

    with tab_ma:
        st.caption("澤豐｜中信｜存現｜🟡 只記帳（僅進月度損益分析，不參與金流核對）")
        col1, col2 = st.columns(2)
        with col1:
            m_ma = st.selectbox("收入月份", months, format_func=_fmt_m, index=1,
                                key="annq_ma_month")
        with col2:
            amt_ma = st.number_input("金額", min_value=0, step=1000,
                                     key="annq_ma_amt")
        if st.button("💾 存檔", type="primary", key="annq_ma_save"):
            if amt_ma <= 0:
                st.error("金額必須 > 0")
            else:
                mo = int(m_ma[5:7])
                desc = f"澤豐{mo}月傳統整復推拿收入"
                _save({
                    "entry_date": m_ma, "scope": "診所",
                    "clinic_id": short_to_cid.get("澤豐"),
                    "form": "存現", "amount": int(amt_ma),
                    "account": "澤豐&個人中信", "description": desc,
                    "category": "memo_only",
                }, desc, "已存傳統整復推拿收入")

    def _cash_income_tab(clinic_short: str, account: str, key_prefix: str):
        st.caption(
            f"{clinic_short}｜中信｜存現｜金流備註。"
            "**現金收入**＝該月記帳總額；**現金給薪扣除**＝以現金支付的醫師薪資；"
            "實際存入銀行＝現金收入 − 現金給薪扣除（系統以此對帳）。"
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            m_sel = st.selectbox("收入月份", months, format_func=_fmt_m, index=1,
                                 key=f"annq_{key_prefix}_month")
        with col2:
            gross = st.number_input("現金收入", min_value=0, step=1000,
                                    key=f"annq_{key_prefix}_gross")
        with col3:
            deduct = st.number_input("現金給薪扣除", min_value=0, step=100,
                                     key=f"annq_{key_prefix}_deduct")
        deposit = int(gross) - int(deduct)
        if gross > 0:
            st.info(f"實際存入銀行核對金額：**NT {deposit:,}** "
                    f"（{int(gross):,} − {int(deduct):,}）")
        if st.button("💾 存檔", type="primary", key=f"annq_{key_prefix}_save"):
            if gross <= 0:
                st.error("現金收入必須 > 0")
            elif deduct > gross:
                st.error("現金給薪扣除不可大於現金收入")
            else:
                mo = int(m_sel[5:7])
                desc_prefix = f"{clinic_short}{mo}月現金收入"
                desc = (f"{desc_prefix}{int(gross):,}，"
                        f"現金給薪扣除{int(deduct):,}")
                _save({
                    "entry_date": _next_month_first(m_sel), "scope": "診所",
                    "clinic_id": short_to_cid.get(clinic_short),
                    "form": "存現", "amount": deposit,
                    "gross_amount": int(gross),
                    "cash_salary_deduction": int(deduct),
                    "account": account, "description": desc,
                    "category": None,
                }, desc_prefix, "已存現金收入")

    with tab_fz:
        _cash_income_tab("澤豐", "澤豐&個人中信", "fz")
    with tab_fp:
        _cash_income_tab("澤沛", "澤沛中信", "fp")

    st.divider()


def _section_manual_annotation():
    """金流補充備註 — 補齊銀行帳戶/帳本未記載的說明（CRUD）"""
    st.subheader("📝 手 KEY：金流補充備註")
    st.caption(
        "用於補齊銀行帳戶/帳本中未記載的備註說明。"
        "例：某筆轉帳實際是「個人借款還款」、某筆存現是「投資收益」。"
        "可隨時查詢/修改/刪除。\n\n"
        "💡 **分類**：\n"
        "- **金流備註** — 預設值，會影響金流辨識（如澤豐現金入帳的對應說明）。\n"
        "- **只記帳** — 不參與任何金流核對，只在「月度損益分析」獨立統計（例：傳統整復推拿收入）。\n"
        "- **股東注資** — 對應澤沛中信實際入帳，但不算經營收入；在「月度損益分析」收入欄會被排除。\n"
        "- **院長私人帳務** — 診所帳戶（如澤豐玉山）裡發生的院長私人進出；"
        "填日期＋金額＋帳戶，該筆銀行交易會**完全排除**於月度實帳金流與"
        "月度損益的收入與支出（實帳金流頁有排除明細可核對）。"
        "⚠️ 金額請填**銀行實際扣款/入帳金額**：跨行轉帳手續費併入扣款時"
        "（同日差 ≤50 元會自動吸收，超過請填實扣額如 3,000,040）；"
        "拆成多筆轉帳要分開 KEY（一筆標記只排一筆交易）。"
    )

    if st.session_state.pop("_ann_just_saved", None):
        st.success("✅ 已儲存")
    if st.session_state.pop("_ann_just_deleted", None):
        st.success("✅ 已刪除")

    sb = get_authed_client()
    clinics_resp = sb.table("clinics").select("id, short_name").execute()
    short_to_cid = {c["short_name"]: c["id"] for c in clinics_resp.data}
    cid_to_short = {v: k for k, v in short_to_cid.items()}

    try:
        rows = (
            sb.table("manual_annotation")
            .select("*")
            .order("entry_date", desc=True)
            .execute().data
        )
    except Exception as e:
        rows = []
        st.error(f"讀取失敗：{e}")

    if rows:
        df = pd.DataFrame(rows)
        df["診所"] = df["clinic_id"].map(cid_to_short).fillna("—")
        if "category" in df.columns:
            df["分類"] = df["category"].map(
                {"memo_only": "🟡 只記帳", "capital_injection": "🔴 股東注資",
                 "director_personal": "🔴 院長私人帳務"}
            ).fillna("金流備註")
        else:
            df["分類"] = "金流備註"
        cols = ["id", "entry_date", "分類", "scope", "form", "account",
                "amount", "診所", "description"]
        present = [c for c in cols if c in df.columns]
        st.markdown(f"**現有 {len(rows)} 筆：**")
        st.dataframe(df[present], use_container_width=True, hide_index=True)
    else:
        st.info("尚無資料")

    if not st.session_state.get("edit_mode"):
        st.info("⚠️ 唯讀模式。如需新增/修改/刪除，請啟用左下「編輯模式」。")
        return

    _ann_quick_templates(sb, rows, short_to_cid)

    st.markdown("**新增 / 修改 / 刪除：**")
    edit_options = ["（新增）"] + [
        f"id={r['id']} {r.get('entry_date', '')} "
        f"{r.get('form') or ''} {r.get('amount') or 0} "
        f"{(r.get('description') or '')[:25]}"
        for r in rows
    ]
    edit_id = st.selectbox(
        "選擇要修改/刪除的列（或留「新增」建立新列）",
        options=edit_options,
        key="ann_edit_select",
    )
    is_edit = edit_id != "（新增）"
    sel = None
    sid = None
    if is_edit:
        try:
            sid = int(edit_id.split()[0].split("=")[1])
            sel = next((r for r in rows if r["id"] == sid), None)
        except Exception:
            sel = None

    forms = ["轉入", "轉出", "存現", "領現"]
    scopes = ["診所", "個人"]
    clinic_opts = ["（不指定）", "澤豐", "澤沛"]
    # 帳戶限定四個（協助解讀帳簿用）
    account_opts = ["澤豐&個人中信", "澤豐玉山", "澤沛中信", "澤沛玉山"]

    col1, col2, col3 = st.columns(3)
    with col1:
        entry_date = st.date_input(
            "日期",
            value=(
                pd.to_datetime(sel["entry_date"]).date()
                if sel and sel.get("entry_date")
                else pd.Timestamp.today().date()
            ),
            key="ann_date",
        )
        scope = st.radio(
            "收支屬性", scopes, horizontal=True,
            index=scopes.index(sel["scope"]) if sel and sel.get("scope") in scopes else 0,
            key="ann_scope",
        )
    with col2:
        cur_clinic = (
            cid_to_short.get(sel["clinic_id"], "（不指定）") if sel else "（不指定）"
        )
        clinic_choice = st.selectbox(
            "診所（scope=診所時必選）",
            options=clinic_opts,
            index=clinic_opts.index(cur_clinic) if cur_clinic in clinic_opts else 0,
            key="ann_clinic",
        )
        form = st.selectbox(
            "形式", forms,
            index=forms.index(sel["form"]) if sel and sel.get("form") in forms else 0,
            key="ann_form",
        )
    with col3:
        amount = st.number_input(
            "金額", min_value=0, step=100,
            value=int(sel["amount"]) if sel and sel.get("amount") else 0,
            key="ann_amount",
        )
        cur_account = (
            sel.get("account") if sel and sel.get("account") in account_opts
            else account_opts[0]
        )
        account = st.selectbox(
            "帳戶",
            options=account_opts,
            index=account_opts.index(cur_account),
            key="ann_account",
            help="僅四個帳戶可選，用於核對帳簿備註",
        )

    cur_category = sel.get("category") if sel else None
    cur_cat_label = _ANN_CATEGORY_LABELS.get(cur_category, _ANN_CATEGORY_OPTIONS[0])
    category_label = st.selectbox(
        "分類（決定條目用途）",
        options=_ANN_CATEGORY_OPTIONS,
        index=_ANN_CATEGORY_OPTIONS.index(cur_cat_label),
        key="ann_category",
        help="只記帳：不影響金流辨識，僅供月度損益分析統計；股東注資：澤沛實際入帳但排除於月度損益分析收入",
    )
    category = _ANN_CATEGORY_BY_LABEL[category_label]

    description = st.text_area(
        "備註說明",
        value=sel.get("description") or "" if sel else "",
        placeholder="例：個人借款還款、廠商紅利、退費、傳統整復推拿收入、股東○○○注資...",
        key="ann_desc",
    )

    save_col, del_col = st.columns(2)
    with save_col:
        if st.button("💾 儲存", type="primary", key="ann_save"):
            if amount <= 0 or not description:
                st.error("金額必須 > 0 且須填備註")
                return
            payload = {
                "entry_date": str(entry_date),
                "scope": scope,
                "clinic_id": (
                    short_to_cid.get(clinic_choice)
                    if clinic_choice != "（不指定）" else None
                ),
                "form": form,
                "amount": int(amount),
                "account": account or None,
                "description": description,
                "category": category,
            }
            try:
                if is_edit and sid:
                    sb.table("manual_annotation").update(payload).eq("id", sid).execute()
                else:
                    sb.table("manual_annotation").insert(payload).execute()
                st.session_state["_ann_just_saved"] = True
                st.rerun()
            except Exception as e:
                st.error(f"儲存失敗：{e}")
    with del_col:
        if is_edit and sid and st.button("🗑️ 刪除", key="ann_del"):
            try:
                sb.table("manual_annotation").delete().eq("id", sid).execute()
                st.session_state["_ann_just_deleted"] = True
                st.rerun()
            except Exception as e:
                st.error(f"刪除失敗：{e}")


def _section_manual_entry():
    """診所非常規收支（CRUD）— 不在銀行明細與 Excel 上的特殊收支"""
    st.subheader("📝 手 KEY：診所非常規收支")
    st.caption(
        "只針對診所的非常規收支（不在銀行明細/Excel 上的）。"
        "例：對帳後不明短少 3000 算入支出、廠商捐贈 10000 現金直接花掉算入收入。"
        "可隨時查詢/修改/刪除。"
    )

    if st.session_state.pop("_me_just_saved", None):
        st.success("✅ 已儲存")
    if st.session_state.pop("_me_just_deleted", None):
        st.success("✅ 已刪除")

    sb = get_authed_client()
    clinics_resp = sb.table("clinics").select("id, short_name").execute()
    short_to_cid = {c["short_name"]: c["id"] for c in clinics_resp.data}
    cid_to_short = {v: k for k, v in short_to_cid.items()}

    try:
        rows = (
            sb.table("manual_entry")
            .select("*")
            .order("entry_date", desc=True)
            .execute().data
        )
    except Exception as e:
        rows = []
        st.error(f"讀取失敗：{e}")

    if rows:
        df = pd.DataFrame(rows)
        df["診所"] = df["clinic_id"].map(cid_to_short).fillna("—")
        cols = ["id", "entry_date", "診所", "direction", "category",
                "amount", "description"]
        present = [c for c in cols if c in df.columns]
        st.markdown(f"**現有 {len(rows)} 筆：**")
        st.dataframe(df[present], use_container_width=True, hide_index=True)
    else:
        st.info("尚無資料")

    if not st.session_state.get("edit_mode"):
        st.info("⚠️ 唯讀模式。如需新增/修改/刪除，請啟用左下「編輯模式」。")
        return

    st.markdown("**新增 / 修改 / 刪除：**")
    edit_options = ["（新增）"] + [
        f"id={r['id']} {r.get('entry_date', '')} "
        f"{r.get('direction') or ''} {r.get('amount') or 0} "
        f"{(r.get('category') or '')[:15]}"
        for r in rows
    ]
    edit_id = st.selectbox(
        "選擇要修改/刪除的列（或留「新增」建立新列）",
        options=edit_options,
        key="me_edit_select",
    )
    is_edit = edit_id != "（新增）"
    sel = None
    sid = None
    if is_edit:
        try:
            sid = int(edit_id.split()[0].split("=")[1])
            sel = next((r for r in rows if r["id"] == sid), None)
        except Exception:
            sel = None

    clinic_opts = ["澤豐", "澤沛"]

    col1, col2, col3 = st.columns(3)
    with col1:
        entry_date = st.date_input(
            "日期",
            value=(
                pd.to_datetime(sel["entry_date"]).date()
                if sel and sel.get("entry_date")
                else pd.Timestamp.today().date()
            ),
            key="me_date",
        )
        cur_clinic = cid_to_short.get(sel["clinic_id"], "澤豐") if sel else "澤豐"
        clinic_choice = st.selectbox(
            "診所",
            options=clinic_opts,
            index=clinic_opts.index(cur_clinic) if cur_clinic in clinic_opts else 0,
            key="me_clinic",
        )
    with col2:
        direction = st.radio(
            "方向", ["income", "expense"], horizontal=True,
            index=0 if (sel and sel.get("direction") == "income") else (
                1 if sel else 0
            ),
            key="me_direction",
        )
        amount = st.number_input(
            "金額", min_value=0, step=100,
            value=int(sel["amount"]) if sel and sel.get("amount") else 0,
            key="me_amount",
        )
    with col3:
        category = st.text_input(
            "類別",
            value=sel.get("category") or "" if sel else "",
            placeholder="例：對帳短少、廠商捐贈、紅利、退款",
            key="me_category",
        )
        description = st.text_area(
            "描述",
            value=sel.get("description") or "" if sel else "",
            key="me_desc",
        )

    save_col, del_col = st.columns(2)
    with save_col:
        if st.button("💾 儲存", type="primary", key="me_save"):
            if amount <= 0:
                st.error("金額必須 > 0")
                return
            payload = {
                "entry_date": str(entry_date),
                "clinic_id": short_to_cid.get(clinic_choice),
                "direction": direction,
                "category": category or None,
                "amount": int(amount),
                "description": description or None,
            }
            try:
                if is_edit and sid:
                    sb.table("manual_entry").update(payload).eq("id", sid).execute()
                else:
                    sb.table("manual_entry").insert(payload).execute()
                st.session_state["_me_just_saved"] = True
                st.rerun()
            except Exception as e:
                st.error(f"儲存失敗：{e}")
    with del_col:
        if is_edit and sid and st.button("🗑️ 刪除", key="me_del"):
            try:
                sb.table("manual_entry").delete().eq("id", sid).execute()
                st.session_state["_me_just_deleted"] = True
                st.rerun()
            except Exception as e:
                st.error(f"刪除失敗：{e}")


def _section_check_expense():
    """支票支出（Sprint 2.7b）— 兩家共用一個檔，每年一檔"""
    from data_processor.expenses import parse_check_expense

    st.subheader("🧾 支票支出（年度檔，兩家共用）")
    st.caption(
        "檔名範例：『@@支票支出115.xlsx』。每列一個年/月，多廠商重複"
        "(廠商/金額/銀行) 三聯欄。銀行「玉延/中延」自動忽略「延」字。"
    )
    uploaded = st.file_uploader(
        "上傳支票支出 xlsx",
        type=["xlsx"], key="check_exp_uploader",
    )
    if not uploaded:
        return
    sb = get_authed_client()
    try:
        records = parse_check_expense(uploaded, uploaded.name)
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return
    if not records:
        st.warning("無可匯入的資料")
        return

    df = pd.DataFrame(records)
    st.success(f"✅ 解析 {len(records)} 筆")

    summary = df.groupby("issue_month", as_index=False).agg(
        筆數=("amount", "count"), 合計=("amount", "sum"),
    )
    st.markdown("**按月份彙總：**")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    by_vendor = df.groupby("vendor", as_index=False).agg(
        筆數=("amount", "count"), 合計=("amount", "sum"),
    ).sort_values("合計", ascending=False)
    st.markdown("**按廠商彙總：**")
    st.dataframe(by_vendor, use_container_width=True, hide_index=True)

    st.markdown("**逐筆預覽：**")
    st.dataframe(
        df[["issue_month", "vendor", "amount", "bank", "note"]],
        use_container_width=True, height=300, hide_index=True,
    )

    if st.button(
        f"💾 確認匯入支票支出（{len(records)} 筆）",
        type="primary", key="check_exp_save",
    ):
        try:
            sb.table("check_expense").upsert(
                records, on_conflict="issue_month,vendor,bank",
            ).execute()
            st.success(f"✅ 寫入 {len(records)} 筆")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _section_inventory_transfer():
    """調貨整理（Sprint 2.7b）— 兩家間實物調撥；amount 待 product_pricing 上線後計算"""
    from data_processor.expenses import parse_inventory_transfer

    st.subheader("🔄 調貨整理（年度檔）")
    st.caption(
        "檔名範例：『澤豐中醫診所調貨整理.xlsx』。系統解析每月區塊的雙欄向"
        "（澤沛 pay 澤豐 / 澤豐 pay 澤沛）。"
        "金額在「金流結算」頁即時試算。"
        "⚠️ 採全月覆蓋：上傳後，檔案內出現的每個月份會先清空再寫入，"
        "同月有品項改動或刪除時請整月重傳。"
    )
    uploaded = st.file_uploader(
        "上傳調貨整理 xlsx",
        type=["xlsx"], key="transfer_uploader",
    )
    if not uploaded:
        return

    sb = get_authed_client()
    clinics = {
        c["short_name"]: c["id"]
        for c in sb.table("clinics").select("id, short_name").execute().data
    }
    fz_id = clinics.get("澤豐")
    fp_id = clinics.get("澤沛")
    if not (fz_id and fp_id):
        st.error("找不到澤豐/澤沛診所")
        return

    try:
        records = parse_inventory_transfer(
            uploaded, uploaded.name,
            clinic_zefeng_id=fz_id, clinic_zepei_id=fp_id,
        )
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return
    if not records:
        st.warning("無可匯入的資料")
        return

    df = pd.DataFrame(records)
    df["方向"] = df["from_clinic_id"].map(
        lambda x: "澤豐→澤沛" if x == fz_id else "澤沛→澤豐"
    )

    st.success(f"✅ 解析 {len(records)} 筆")

    summary = df.groupby(["transfer_month", "方向"], as_index=False).size()
    summary.columns = ["月份", "方向", "筆數"]
    st.markdown("**按月份+方向彙總：**")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.markdown("**逐筆預覽：**")
    st.dataframe(
        df[["transfer_month", "方向", "item", "qty"]],
        use_container_width=True, height=400, hide_index=True,
    )

    if st.button(
        f"💾 確認匯入調貨（{len(records)} 筆，金額暫空）",
        type="primary", key="transfer_save",
    ):
        try:
            # 同一批內依 UNIQUE 鍵 (transfer_month, from_clinic_id,
            # to_clinic_id, item) 去重：同月+同方向+同品項出現多筆時
            # 把 qty 加總（同一鍵在一個 upsert command 內只能有一列，
            # 否則 Postgres 會丟 21000「cannot affect row a second time」）。
            merged: dict[tuple, dict] = {}
            for r in records:
                key = (
                    r["transfer_month"], r["from_clinic_id"],
                    r["to_clinic_id"], r["item"],
                )
                if key in merged:
                    merged[key]["qty"] = round(
                        merged[key]["qty"] + r["qty"], 2
                    )
                else:
                    merged[key] = {
                        k: v for k, v in r.items() if k != "方向"
                    }
            payload = list(merged.values())
            # ── 全月覆蓋：先清除檔案內各月的兩家舊調貨資料，再寫入 ──
            # （同月若有品項改動/刪除，舊列才不會殘留，例如已刪除的清肺飲）
            months = sorted({r["transfer_month"] for r in payload})
            sb.table("inventory_transfer").delete() \
                .in_("transfer_month", months) \
                .in_("from_clinic_id", [fz_id, fp_id]) \
                .in_("to_clinic_id", [fz_id, fp_id]) \
                .execute()
            sb.table("inventory_transfer").insert(payload).execute()
            dup = len(records) - len(payload)
            mrange = f"{months[0][:7]}~{months[-1][:7]}" if months else "-"
            msg = (
                f"✅ 全月覆蓋寫入 {len(payload)} 筆"
                f"（涵蓋 {len(months)} 個月：{mrange}，已清除這些月份的舊資料）"
            )
            if dup:
                msg += f"；已合併 {dup} 筆同鍵重複（qty 加總）"
            st.success(msg)
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _section_outpatient_report():
    """門診申報金額統計報表 + A91+複針補表（Sprint 2.4）"""
    from data_processor.clinic_report import (
        detect_format,
        parse_fz_main, parse_main16, parse_a91,
    )

    st.subheader("📊 門診申報金額統計報表 + A91+複針（批次）")
    st.caption(
        "版式自動識別（澤豐自 11507 起改用與澤沛相同醫資系統）："
        "16 欄主表 + A91+複針 137 欄補表（兩家通用）；"
        "澤豐 ≤11506 為 48 欄舊主表（已含 A91/複針，無補表）。"
        "可一次選多份；補表會 partial update 到主表已存在的列。"
    )

    uploaded_files = st.file_uploader(
        "上傳一份或多份 xlsx",
        type=["xlsx"],
        accept_multiple_files=True,
        key="outpatient_uploader",
    )
    if not uploaded_files:
        return

    sb = get_authed_client()
    clinics_resp = sb.table("clinics").select("id, short_name").execute()
    short_to_cid = {c["short_name"]: c["id"] for c in clinics_resp.data}
    doctors_resp = sb.table("doctors").select("id, name").execute()
    name_to_did = {d["name"]: d["id"] for d in doctors_resp.data}

    main_records: list[dict] = []
    a91_records: list[dict] = []
    summaries: list[dict] = []
    errors: list[str] = []

    parser_map = {
        "fz_main48": parse_fz_main,
        "main16": parse_main16,
        "a91_137": parse_a91,
    }
    kind_label = {
        "fz_main48": "48 欄主表（舊制 ≤11506）",
        "main16": "16 欄主表",
        "a91_137": "A91+複針 137 欄補表",
    }

    for f in uploaded_files:
        try:
            meta = detect_format(f.name)
            cid = short_to_cid[meta["clinic_short"]]
            recs = parser_map[meta["kind"]](f, f.name, cid, name_to_did)
            if meta["kind"] == "a91_137":
                a91_records.extend(recs)
            else:
                main_records.extend(recs)
            summaries.append({
                "檔名": f.name,
                "版式": f"{meta['clinic_short']} {kind_label[meta['kind']]}",
                "服務月": meta["service_month"],
                "醫師數": len(recs),
            })
        except Exception as e:
            errors.append(f"{f.name}：{e}")

    if errors:
        st.error("部分檔案解析失敗：")
        for e in errors:
            st.code(e)
    if not summaries:
        return

    st.markdown("**檔案彙整：**")
    st.dataframe(pd.DataFrame(summaries), use_container_width=True, hide_index=True)

    cid_to_short = {v: k for k, v in short_to_cid.items()}
    did_to_name = {d["id"]: d["name"] for d in doctors_resp.data}

    if main_records:
        st.markdown("**主表預覽：**")
        df = pd.DataFrame(main_records).copy()
        df["診所"] = df["clinic_id"].map(cid_to_short)
        df["醫師"] = df["doctor_id"].map(did_to_name)
        cols = [
            "service_month", "診所", "醫師",
            "nhi_consult_fee", "nhi_drug_fee", "nhi_treatment_fee",
            "nhi_lab_fee", "nhi_total_points",
            "cash_internal", "cash_acupuncture", "registration_fee",
            "acu_complex_mid_count", "acu_complex_high_count", "a91_count",
        ]
        present = [c for c in cols if c in df.columns]
        st.dataframe(df[present], use_container_width=True, hide_index=True)

    if a91_records:
        st.markdown("**A91+複針 補表預覽（將 partial update 到主表）：**")
        df = pd.DataFrame(a91_records).copy()
        df["診所"] = df["clinic_id"].map(cid_to_short)
        df["醫師"] = df["doctor_id"].map(did_to_name)
        cols = [
            "service_month", "診所", "醫師",
            "acu_complex_mid_count", "acu_complex_high_count", "a91_count",
        ]
        st.dataframe(df[cols], use_container_width=True, hide_index=True)

    if st.button(
        f"💾 確認匯入（主表 {len(main_records)} 筆 / 補表 {len(a91_records)} 筆）",
        type="primary",
        key="outpatient_import_btn",
    ):
        _import_outpatient_records(sb, main_records, a91_records)


def _import_outpatient_records(
    sb,
    main_records: list[dict],
    a91_records: list[dict],
):
    """主表整列 upsert + 補表 partial update（只三欄）"""
    errors: list[str] = []

    if main_records:
        # 48 欄舊制與 16 欄新制的欄位集合不同，混批 upsert 需補齊缺鍵
        # （皆為數值欄；缺鍵＝該版式報表無此欄位，補 0 語意正確）
        all_keys = set().union(*(r.keys() for r in main_records))
        for r in main_records:
            for k in all_keys - r.keys():
                r[k] = 0
        try:
            sb.table("doctor_outpatient_summary").upsert(
                main_records,
                on_conflict="clinic_id,doctor_id,service_month",
            ).execute()
            st.success(f"✅ 主表寫入 {len(main_records)} 筆")
        except Exception as e:
            errors.append(f"主表：{e}")

    a91_done = 0
    for rec in a91_records:
        try:
            existing = (
                sb.table("doctor_outpatient_summary")
                .select("id")
                .eq("clinic_id", rec["clinic_id"])
                .eq("doctor_id", rec["doctor_id"])
                .eq("service_month", rec["service_month"])
                .execute()
            )
            update_payload = {
                "acu_complex_mid_count": rec["acu_complex_mid_count"],
                "acu_complex_high_count": rec["acu_complex_high_count"],
                "a91_count": rec["a91_count"],
            }
            if existing.data:
                (
                    sb.table("doctor_outpatient_summary")
                    .update(update_payload)
                    .eq("clinic_id", rec["clinic_id"])
                    .eq("doctor_id", rec["doctor_id"])
                    .eq("service_month", rec["service_month"])
                    .execute()
                )
            else:
                payload = {
                    "clinic_id": rec["clinic_id"],
                    "doctor_id": rec["doctor_id"],
                    "service_month": rec["service_month"],
                    **update_payload,
                }
                sb.table("doctor_outpatient_summary").insert(payload).execute()
            a91_done += 1
        except Exception as e:
            errors.append(
                f"補表 (clinic={rec['clinic_id']}, doctor={rec['doctor_id']}, "
                f"month={rec['service_month']})：{e}"
            )
    if a91_records:
        st.success(f"✅ A91+複針 補表處理 {a91_done}/{len(a91_records)} 筆")

    if errors:
        st.error("部分批次失敗：")
        for e in errors:
            st.code(e)
    elif main_records or a91_records:
        st.balloons()


def _section_visit_count():
    """健保人數+初診統計批次上傳區（Sprint 2.5）— 薪資業績獎金 + 診數來源"""
    from data_processor.visit_count import (
        parse_filename as parse_vc_filename,
        parse_visit_count,
    )

    st.subheader("👥 健保人數+初診統計（批次）")
    st.caption(
        "提供薪資計算的「診數」+ 業績獎金「健保人次」。"
        "可一次選多份不同月份/診所的檔案；診所由檔名自動識別。"
    )

    uploaded_files = st.file_uploader(
        "上傳一份或多份 xlsx",
        type=["xlsx"],
        accept_multiple_files=True,
        key="vc_uploader",
    )
    if not uploaded_files:
        return

    sb = get_authed_client()
    clinics_resp = sb.table("clinics").select("id, short_name").execute()
    short_to_cid = {c["short_name"]: c["id"] for c in clinics_resp.data}
    cid_to_short = {c["id"]: c["short_name"] for c in clinics_resp.data}

    doctors_resp = sb.table("doctors").select("id, name").execute()
    name_to_did = {d["name"]: d["id"] for d in doctors_resp.data}

    all_doctor_records: list[dict] = []
    all_clinic_rates: list[dict] = []
    summaries: list[dict] = []
    errors: list[str] = []

    for f in uploaded_files:
        try:
            meta = parse_vc_filename(f.name)
            cid = short_to_cid.get(meta["clinic_short"])
            if cid is None:
                raise ValueError(f"檔名診所 {meta['clinic_short']} 不在 clinics 表")
            doc_recs, clinic_rates = parse_visit_count(
                f, f.name, cid, name_to_did,
            )
            all_doctor_records.extend(doc_recs)
            if clinic_rates:
                all_clinic_rates.append(clinic_rates)
            summaries.append({
                "檔名": f.name,
                "診所": meta["clinic_short"],
                "服務月": meta["service_month"],
                "醫師數": len(doc_recs),
                "診所彙總": "✅" if clinic_rates else "—",
            })
        except Exception as e:
            errors.append(f"{f.name}：{e}")

    if errors:
        st.error("部分檔案解析失敗：")
        for e in errors:
            st.code(e)

    if not summaries:
        return

    st.markdown("**檔案彙整：**")
    st.dataframe(pd.DataFrame(summaries), use_container_width=True, hide_index=True)

    if all_doctor_records:
        # 預覽（依檔名解析後加入醫師名顯示）
        did_to_name = {d["id"]: d["name"] for d in doctors_resp.data}
        preview = pd.DataFrame(all_doctor_records).copy()
        preview["診所"] = preview["clinic_id"].map(cid_to_short)
        preview["醫師"] = preview["doctor_id"].map(did_to_name)
        cols = [
            "service_month", "診所", "醫師", "sessions_total",
            "nhi_internal", "nhi_pure_acu", "nhi_pure_trauma",
            "nhi_internal_acu", "nhi_internal_trauma", "nhi_visits_total",
            "cash_visits_internal", "cash_visits_acupuncture", "total_visits",
        ]
        st.markdown("**醫師月度資料預覽：**")
        st.dataframe(preview[cols], use_container_width=True, height=250)

    if all_clinic_rates:
        st.markdown("**診所月度彙總（初診率/自費率/掛號優免）預覽：**")
        rates_df = pd.DataFrame(all_clinic_rates).copy()
        rates_df["診所"] = rates_df["clinic_id"].map(cid_to_short)
        cols = [
            "service_month", "診所",
            "first_visit_count", "first_visit_rate",
            "revisit_count", "revisit_rate",
            "cash_visit_count", "cash_visit_rate",
            "free_reg_count", "free_reg_rate",
        ]
        present = [c for c in cols if c in rates_df.columns]
        st.dataframe(rates_df[present], use_container_width=True, hide_index=True)

    if st.button(
        f"💾 確認匯入（醫師 {len(all_doctor_records)} 筆 + 診所彙總 {len(all_clinic_rates)} 筆）",
        type="primary",
        key="vc_import_btn",
    ):
        _import_visit_records(sb, all_doctor_records, all_clinic_rates)


def _import_visit_records(
    sb,
    doctor_records: list[dict],
    clinic_rates: list[dict],
):
    """寫入 doctor_visit_stats（依 clinic+doctor+month UNIQUE）+ clinic_visit_rates"""
    errors: list[str] = []

    # 醫師月度
    if doctor_records:
        try:
            sb.table("doctor_visit_stats").upsert(
                doctor_records,
                on_conflict="clinic_id,doctor_id,service_month",
            ).execute()
            st.success(f"✅ 醫師月度資料寫入 {len(doctor_records)} 筆")
        except Exception as e:
            errors.append(f"doctor_visit_stats：{e}")

    # 診所彙總
    if clinic_rates:
        try:
            sb.table("clinic_visit_rates").upsert(
                clinic_rates,
                on_conflict="clinic_id,service_month",
            ).execute()
            st.success(f"✅ 診所彙總寫入 {len(clinic_rates)} 筆")
        except Exception as e:
            errors.append(f"clinic_visit_rates：{e}")

    if errors:
        st.error("部分批次失敗：")
        for e in errors:
            st.code(e)
    elif doctor_records or clinic_rates:
        st.balloons()


def _section_capacity_quota():
    """合理門診量批次上傳（11503+）"""
    from data_processor.capacity_quota import (
        parse_filename as parse_cq_filename,
        parse_capacity_quota,
    )

    st.subheader("📊 合理門診量（批次）")
    st.caption(
        "檔名範例：『11503澤豐合理門診量.xlsx』。"
        "5 階段(1-30 / 31-50 / 51-70 / 71-150 / 151-1000)。"
        "「補支援醫師數」從末階段往前扣，被扣量自動歸給該診所支援醫師"
        "（從醫師主檔 role='support' 配置抓）。"
    )

    uploaded_files = st.file_uploader(
        "上傳一份或多份 xlsx",
        type=["xlsx"],
        accept_multiple_files=True,
        key="cq_uploader",
    )
    if not uploaded_files:
        return

    sb = get_authed_client()
    clinics_resp = sb.table("clinics").select("id, short_name").execute()
    short_to_cid = {c["short_name"]: c["id"] for c in clinics_resp.data}
    cid_to_short = {c["id"]: c["short_name"] for c in clinics_resp.data}

    doctors_resp = sb.table("doctors").select("id, name").execute()
    name_to_did = {d["name"]: d["id"] for d in doctors_resp.data}
    did_to_name = {d["id"]: d["name"] for d in doctors_resp.data}

    # 該診所支援醫師（role='support'，且支援期間涵蓋檔案月份）
    from data_processor.doctor_config import fetch_doctor_clinic, active_dc_rows
    all_dc_rows = fetch_doctor_clinic(sb)

    def _support_by_clinic(service_month: str) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for r in active_dc_rows(all_dc_rows, service_month):
            if r["role"] == "support":
                out.setdefault(r["clinic_id"], []).append(r["doctor_id"])
        return out

    all_records: list[dict] = []
    summaries: list[dict] = []
    errors: list[str] = []
    months_per_clinic: set[tuple[int, str]] = set()

    for f in uploaded_files:
        try:
            meta = parse_cq_filename(f.name)
            cid = short_to_cid.get(meta["clinic_short"])
            if cid is None:
                raise ValueError(f"檔名診所 {meta['clinic_short']} 不在 clinics 表")
            sup_list = _support_by_clinic(meta["service_month"]).get(cid, [])
            sup_did = sup_list[0] if sup_list else None
            sup_warn = (
                f"⚠️ 該診所有 {len(sup_list)} 位 role='support'，先取 "
                f"{did_to_name.get(sup_list[0], '?')}"
                if len(sup_list) > 1 else
                "⚠️ 該診所未配置 role='support' 支援醫師，被扣量無法歸屬"
                if not sup_list else None
            )
            recs, info = parse_capacity_quota(
                f, f.name, cid, name_to_did, sup_did,
            )
            all_records.extend(recs)
            months_per_clinic.add((cid, info["service_month"]))
            row_count = sum(1 for r in recs if not r.get("is_support"))
            sup_count = sum(1 for r in recs if r.get("is_support"))
            summaries.append({
                "檔名": f.name,
                "診所": meta["clinic_short"],
                "服務月": info["service_month"],
                "正職醫師": row_count,
                "支援醫師 row": sup_count,
                "補支援總數": info["total_offset"],
                "支援歸屬": did_to_name.get(sup_did) if sup_did else "—",
                "未識別": ",".join(info.get("unknown_doctors") or []) or "—",
                "備註": sup_warn or "",
            })
        except Exception as e:
            errors.append(f"{f.name}：{e}")

    if errors:
        st.error("部分檔案解析失敗：")
        for e in errors:
            st.code(e)
    if not summaries:
        return

    st.markdown("**檔案彙整：**")
    st.dataframe(pd.DataFrame(summaries), use_container_width=True, hide_index=True)

    if all_records:
        preview = pd.DataFrame(all_records).copy()
        preview["診所"] = preview["clinic_id"].map(cid_to_short)
        preview["醫師"] = preview["doctor_id"].map(did_to_name)
        preview["角色"] = preview["is_support"].map({True: "支援", False: "主聘"})
        cols = [
            "service_month", "診所", "醫師", "角色",
            "stage1", "stage2", "stage3", "stage4", "stage5",
            "support_offset",
        ]
        st.markdown("**醫師資料預覽：**")
        st.dataframe(preview[cols], use_container_width=True, hide_index=True)

    if st.button(
        f"💾 確認匯入（{len(all_records)} 筆，月份覆蓋）",
        type="primary",
        key="cq_import_btn",
    ):
        _import_capacity_quota(sb, all_records, months_per_clinic)


def _import_capacity_quota(
    sb,
    records: list[dict],
    months_per_clinic: set[tuple[int, str]],
):
    """月份覆蓋寫入 doctor_capacity_stage：先 DELETE 再 INSERT。"""
    if not records:
        return
    try:
        # 先刪除涉及的 (clinic_id, service_month) 既有列
        for cid, sm in months_per_clinic:
            sb.table("doctor_capacity_stage").delete().eq(
                "clinic_id", cid
            ).eq("service_month", sm).execute()
        # 再 insert
        sb.table("doctor_capacity_stage").insert(records).execute()
        st.success(f"✅ 寫入 {len(records)} 筆，覆蓋 {len(months_per_clinic)} 個 (診所×月)")
        st.balloons()
    except Exception as e:
        st.error(f"寫入失敗：{e}")


def _import_cash_records(sb, records: list[dict]):
    """寫入 doctor_cash_visits（依 raw_row_hash UNIQUE 防重複）"""
    inserted = 0
    skipped = 0
    errors = []
    progress = st.progress(0, text="匯入中...")
    total = len(records)

    BATCH = 100
    for i in range(0, total, BATCH):
        batch = records[i:i + BATCH]
        try:
            resp = (
                sb.table("doctor_cash_visits")
                .upsert(batch, on_conflict="raw_row_hash", ignore_duplicates=True)
                .execute()
            )
            new = len(resp.data) if resp.data else 0
            inserted += new
            skipped += len(batch) - new
        except Exception as e:
            errors.append(f"批次 {i}-{i + len(batch)}：{e}")
        progress.progress(min((i + BATCH) / total, 1.0))
    progress.empty()

    if errors:
        st.error("部分批次失敗：")
        for e in errors:
            st.code(e)
    if inserted:
        st.success(f"✅ 新增 {inserted} 筆")
    if skipped:
        st.info(f"ℹ️ 跳過重複 {skipped} 筆（依 raw_row_hash）")
    if inserted and not errors:
        st.balloons()


def _import_bank_records(sb, records: list[dict]):
    """寫入 bank_transactions（用 upsert + ignore_duplicates 防重複）"""
    inserted = 0
    skipped = 0
    errors = []
    progress = st.progress(0, text="匯入中...")
    total = len(records)

    BATCH_SIZE = 50
    for i in range(0, total, BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        try:
            resp = (
                sb.table("bank_transactions")
                .upsert(batch, on_conflict="raw_row_hash", ignore_duplicates=True)
                .execute()
            )
            new_count = len(resp.data) if resp.data else 0
            inserted += new_count
            skipped += len(batch) - new_count
        except Exception as e:
            errors.append(f"批次 {i}-{i+len(batch)}：{e}")
        progress.progress(min((i + BATCH_SIZE) / total, 1.0))

    progress.empty()

    if errors:
        st.error("部分匯入失敗：")
        for err in errors:
            st.code(err)
    if inserted:
        st.success(f"✅ 新增 {inserted} 筆")
    if skipped:
        st.info(f"ℹ️ 跳過重複 {skipped} 筆")
    if inserted and not errors:
        st.balloons()


# ============================================================
# 4. 醫師薪資（Phase 3.5）
# ============================================================
def _warn_missing_a91_supplement(inputs: dict, service_month: str):
    """複針/A91 獎金依賴 A91+複針補表（澤沛 11504 起；澤豐 11507 改新系統起）。
    若該診所主表已入庫但複針/A91 全為 0，多半是補表尚未上傳 → 提醒。"""
    from data_processor.salary import ACU_A91_EFFECTIVE_FROM
    from data_processor.clinic_report import FZ_NEW_SYSTEM_FROM_MONTH

    if service_month < ACU_A91_EFFECTIVE_FROM:
        return
    roc_ym = f"{int(service_month[:4]) - 1911}{service_month[5:7]}"
    for cid, c in inputs["clinics"].items():
        short = c["short_name"]
        if short == "澤豐" and service_month < FZ_NEW_SYSTEM_FROM_MONTH:
            continue  # 澤豐舊制 48 欄主表已含複針/A91，無補表
        rows = [v for (k_cid, _), v in inputs["outpatient"].items() if k_cid == cid]
        if not rows:
            st.warning(
                f"⚠️ {short} {roc_ym} 門診申報主表尚未上傳，"
                "複針/A91 獎金將計為 0。"
            )
        elif not any(
            (r.get("acu_complex_mid_count") or 0)
            + (r.get("acu_complex_high_count") or 0)
            + (r.get("a91_count") or 0)
            for r in rows
        ):
            st.warning(
                f"⚠️ {short} {roc_ym} 複針/A91 人數全為 0 —"
                f"「{roc_ym}{short}A91+複針.xlsx」補表可能尚未上傳，"
                "相關獎金將計為 0。"
            )


def page_salary():
    st.title("💵 醫師薪資計算")

    from data_processor.salary import (
        run_salary_calculation,
        upsert_salary_monthly,
        fetch_salary_inputs,
    )

    sb = get_authed_client()

    # ─── 月份選擇 ───
    months_resp = (
        sb.table("doctor_visit_stats")
        .select("service_month")
        .order("service_month", desc=True)
        .execute()
    )
    months_set = sorted(
        _filter_min_month({r["service_month"] for r in months_resp.data}),
        reverse=True,
    )
    if not months_set:
        st.warning("⚠️ 尚無資料，請先到「本月資料匯入」上傳。")
        return

    col1, _ = st.columns([2, 5])
    with col1:
        service_month = st.selectbox(
            "服務月份", months_set,
            format_func=lambda d: d[:7], key="salary_month",
        )

    with st.spinner("計算中..."):
        components, payslips = run_salary_calculation(sb, service_month)
        inputs = fetch_salary_inputs(sb, service_month)
        cash_lookup = inputs["cash_monthly"]

    if not components:
        st.warning("該月份無計算結果")
        return

    _warn_missing_a91_supplement(inputs, service_month)

    # ════════════════════════════════════════════════════════
    # PART 1：彙總比較表（網頁儀表板模式）
    # ════════════════════════════════════════════════════════

    st.subheader("📋 醫師月薪結構（應付 → 扣除 → 實領）")
    pay_rows = []
    for p in sorted(payslips, key=lambda x: (x.main_clinic_name, x.doctor_name)):
        pay_rows.append({
            "主聘": p.main_clinic_name,
            "醫師": p.doctor_name,
            "主聘應付": p.gross_main,
            "支援應付": p.gross_support,
            "支援來自": p.support_clinic_name or "—",
            "應付合計": p.gross_total,
            "勞保扣": p.labor_deduction,
            "健保扣": p.nhi_deduction,
            "實領": p.take_home,
            "扣除備註": p.insurance_note or "",
        })
    st.dataframe(
        pd.DataFrame(pay_rows), use_container_width=True, hide_index=True,
    )
    if any(p.insurance_note for p in payslips):
        st.caption(
            "ℹ️ 扣除備註：勞保按日比例 = 扣除額/30 × 當月在保天數（整月在保扣全額）；"
            "健保當月任一天在保即扣全額。"
        )

    with st.expander("📊 分診所薪資明細（應付組成）"):
        comp_rows = []
        for c in sorted(components, key=lambda x: (x.doctor_name, x.clinic_name)):
            comp_rows.append({
                "診所": c.clinic_name,
                "醫師": c.doctor_name,
                "角色": c.role,
                "院長津貼": c.director_allowance,
                "診數": c.sessions_total,
                "診薪×診數": c.session_pay,
                "自費抽成": c.commission_total,
                "業績獎金": c.bonus_total,
                "複針獎金": c.acu_complex_bonus,
                "A91獎金": c.a91_bonus,
                "平均人次": c.avg_visits_per_session,
                "業績觸發": "✅" if c.perf_triggered else "—",
                "應付小計": c.gross,
                "備註": "; ".join(c.notes) if c.notes else "",
            })
        st.dataframe(
            pd.DataFrame(comp_rows), use_container_width=True, hide_index=True
        )

    cross = [p for p in payslips if p.support_clinic_id and p.gross_support > 0]
    if cross:
        with st.expander("💱 跨支援墊付（豐沛金流項目）"):
            cross_rows = [
                {
                    "墊付方（主聘）": p.main_clinic_name,
                    "應由（看診診所）還": p.support_clinic_name,
                    "醫師": p.doctor_name,
                    "金額": p.gross_support,
                }
                for p in cross
            ]
            st.dataframe(
                pd.DataFrame(cross_rows), use_container_width=True, hide_index=True
            )

    with st.expander("💰 自費抽成各項目明細"):
        rows = []
        for c in sorted(components, key=lambda x: (x.doctor_name, x.clinic_name)):
            row = {"診所": c.clinic_name, "醫師": c.doctor_name}
            row.update(c.commission_breakdown)
            row["合計"] = c.commission_total
            rows.append(row)
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True
        )

    triggered = [c for c in components if c.perf_triggered]
    if triggered:
        with st.expander("🎯 業績獎金明細（觸發者）"):
            rows = [
                {
                    "診所": c.clinic_name, "醫師": c.doctor_name,
                    "平均健保人次": c.avg_visits_per_session,
                    "內科業績": c.bonus_internal,
                    "純針純傷業績": c.bonus_pure_acu_trauma,
                    "內+組合業績": c.bonus_internal_combo,
                    "業績合計": c.bonus_total,
                }
                for c in triggered
            ]
            st.dataframe(
                pd.DataFrame(rows), use_container_width=True, hide_index=True
            )

    has_acu = any(c.acu_complex_bonus or c.a91_bonus for c in components)
    if has_acu:
        with st.expander("💉 複針/A91 獎金細項（115/04 起新制）"):
            rows = []
            for c in sorted(components, key=lambda x: (x.doctor_name, x.clinic_name)):
                if not (c.acu_complex_bonus or c.a91_bonus
                        or c.acu_complex_mid_count or c.a91_count):
                    continue
                rows.append({
                    "診所": c.clinic_name, "醫師": c.doctor_name,
                    "中複針人數": c.acu_complex_mid_count,
                    "高複針人數": c.acu_complex_high_count,
                    "複針獎金": c.acu_complex_bonus,
                    "A91人數": c.a91_count,
                    "A91獎金": c.a91_bonus,
                    "合計": c.acu_complex_bonus + c.a91_bonus,
                })
            if rows:
                st.dataframe(
                    pd.DataFrame(rows), use_container_width=True, hide_index=True
                )

    # ════════════════════════════════════════════════════════
    # PART 2：列印薪資單（給醫師看的卡片格式）
    # ════════════════════════════════════════════════════════

    st.divider()
    st.subheader("🖨️ 列印薪資單（給醫師閱覽用）")
    st.caption("選一位醫師顯示完整薪資單，可直接 Ctrl+P 列印或截圖。")

    by_doctor: dict[int, list] = {}
    for c in components:
        by_doctor.setdefault(c.doctor_id, []).append(c)

    doctor_options = sorted(
        by_doctor.keys(),
        key=lambda did: by_doctor[did][0].doctor_name,
    )
    did_to_name = {did: by_doctor[did][0].doctor_name for did in doctor_options}

    SHOW_NONE = "__none__"
    SHOW_ALL = "__all__"

    def _fmt(x):
        if x == SHOW_NONE:
            return "（不顯示）"
        if x == SHOW_ALL:
            return "🖨️ 全部展開（一頁可 Ctrl+P 列印）"
        return did_to_name[x]

    selected_doctor = st.selectbox(
        "選擇醫師",
        options=[SHOW_NONE, SHOW_ALL] + list(doctor_options),
        format_func=_fmt,
        key="payslip_doctor_select",
    )

    role_label = {"director": "負責醫", "regular": "執業醫", "support": "支援醫"}

    def _render_one_doctor(doctor_id):
        comps = by_doctor[doctor_id]
        ps = next((p for p in payslips if p.doctor_id == doctor_id), None)
        doctor_name = comps[0].doctor_name

        st.markdown(f"## 🩺 {doctor_name}　薪資單　{service_month[:7]}")

        if len(comps) > 1:
            comps_sorted = sorted(
                comps, key=lambda c: 0 if c.role != "support" else 1
            )
            cols_layout = st.columns(len(comps_sorted))
            for i, c in enumerate(comps_sorted):
                with cols_layout[i]:
                    if c.role != "support" and ps:
                        _render_payslip_block(
                            c, cash_lookup, role_label,
                            ps.labor_deduction, ps.nhi_deduction,
                        )
                    else:
                        _render_payslip_block(c, cash_lookup, role_label)
        else:
            c = comps[0]
            if c.role != "support" and ps:
                _render_payslip_block(
                    c, cash_lookup, role_label,
                    ps.labor_deduction, ps.nhi_deduction,
                )
            else:
                _render_payslip_block(c, cash_lookup, role_label)

        if ps and ps.support_clinic_id:
            main_take = ps.gross_main - ps.labor_deduction - ps.nhi_deduction
            st.markdown("---")
            st.markdown(
                f"### 📊 兩診所合計（主聘已扣勞健保，支援未扣）\n\n"
                f"{ps.main_clinic_name} 實領 NT {main_take:,} 元　＋　"
                f"{ps.support_clinic_name} 應付 NT {ps.gross_support:,} 元"
            )
            st.markdown(f"## **實領總額：NT {ps.take_home:,} 元**")

        # ─── 此醫師專屬 列印 HTML 下載 ───
        html_str = generate_doctor_payslip_html(
            comps, ps, cash_lookup, role_label, service_month
        )
        st.download_button(
            f"📄 下載 {doctor_name} 薪資單 HTML（開啟後 Ctrl+P 列印乾淨版面）",
            data=html_str.encode("utf-8"),
            file_name=f"薪資單_{doctor_name}_{service_month[:7]}.html",
            mime="text/html",
            key=f"dl_html_{doctor_id}",
        )
        # 現金支付版：末尾多 匯款金額(=投保額)/現金給付(=實領−匯款)/簽收欄
        if ps:
            html_cash = generate_doctor_payslip_html(
                comps, ps, cash_lookup, role_label, service_month,
                cash_payment=True,
            )
            st.download_button(
                f"📄 下載 {doctor_name} 薪資單 HTML-現金支付（開啟後 Ctrl+P 列印乾淨版面）",
                data=html_cash.encode("utf-8"),
                file_name=f"薪資單_現金支付_{doctor_name}_{service_month[:7]}.html",
                mime="text/html",
                key=f"dl_html_cash_{doctor_id}",
            )

    if selected_doctor == SHOW_ALL:
        st.info(
            "📄 一頁顯示所有醫師薪資單。瀏覽器 Ctrl+P 可印出整頁，"
            "或下載下方的 markdown 檔自行匯入 Word/Google Doc。"
        )
        for i, did in enumerate(doctor_options):
            if i > 0:
                st.markdown("---\n\n")
            _render_one_doctor(did)
        # 提供下載：把所有醫師合併成一份 markdown
        all_md_lines: list[str] = [f"# 醫師薪資單　{service_month[:7]}", ""]
        for did in doctor_options:
            comps = by_doctor[did]
            ps = next((p for p in payslips if p.doctor_id == did), None)
            all_md_lines.append(f"\n\n## 🩺 {comps[0].doctor_name}　薪資單　{service_month[:7]}\n")
            comps_sorted = sorted(
                comps, key=lambda c: 0 if c.role != "support" else 1
            )
            for c in comps_sorted:
                if c.role != "support" and ps:
                    block = _payslip_lines(
                        c, cash_lookup, role_label,
                        ps.labor_deduction, ps.nhi_deduction,
                    )
                else:
                    block = _payslip_lines(c, cash_lookup, role_label)
                all_md_lines.extend(block)
                all_md_lines.append("")
            if ps and ps.support_clinic_id:
                main_take = ps.gross_main - ps.labor_deduction - ps.nhi_deduction
                all_md_lines.append(
                    f"\n**📊 兩診所合計**：{ps.main_clinic_name} 實領 NT "
                    f"{main_take:,} 元 ＋ {ps.support_clinic_name} 應付 NT "
                    f"{ps.gross_support:,} 元"
                )
                all_md_lines.append(f"## 實領總額：NT {ps.take_home:,} 元")
            all_md_lines.append("\n---\n")
        st.download_button(
            "📥 下載所有醫師薪資單 (.md)",
            data="\n".join(all_md_lines).encode("utf-8"),
            file_name=f"薪資單_{service_month[:7]}.md",
            mime="text/markdown",
            key="dl_all_md",
        )
    elif selected_doctor != SHOW_NONE:
        st.markdown("---")
        _render_one_doctor(selected_doctor)

    # ════════════════════════════════════════════════════════
    # PART 3：寫入 DB
    # ════════════════════════════════════════════════════════

    st.divider()
    if not st.session_state.get("edit_mode"):
        st.info("以上為即時試算。如需寫入 doctor_salary_monthly，啟用編輯模式後再回此頁。")
        return

    st.warning("⚠️ 寫入會覆蓋同月份既有計算結果")
    if st.button(
        f"💾 寫入 {service_month[:7]} 到 doctor_salary_monthly",
        type="primary", key=f"salary_save_{service_month}",
    ):
        try:
            n = upsert_salary_monthly(sb, components, payslips)
            st.success(f"✅ 寫入 {n} 筆")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _payslip_lines(c, cash_lookup: dict, role_label: dict,
                   labor_ded: int = 0, nhi_ded: int = 0) -> list[str]:
    """
    產生(診所×醫師)薪資單 markdown 行 list。
    避開 $ 符號（streamlit markdown 會啟動 LaTeX 公式渲染導致顯示亂掉）—
    改用「[a × b% = c] 元」格式。

    若主聘那欄傳入 labor_ded/nhi_ded 則顯示扣除 + 實領；支援欄不傳。
    """
    role = role_label.get(c.role, c.role)
    cash_row = cash_lookup.get((c.clinic_id, c.doctor_id), {}) or {}
    L: list[str] = []

    L.append(f"### {c.clinic_name}　{role}")

    if c.sessions_total or c.visit_count_nhi:
        L.append(
            f"**看診**：診數 {c.sessions_total}　|　"
            f"健保人次 {c.visit_count_nhi:,}　|　"
            f"平均 {c.avg_visits_per_session}/診"
        )

    if c.session_pay:
        L.append(f"**診薪**：總額 **NT {c.session_pay:,} 元**")

    if c.director_allowance:
        L.append(f"**負責醫津貼**：**NT {c.director_allowance:,} 元**")

    # 業績獎金
    perf_lines = []
    perf_active = c.perf_triggered
    if c.bonus_internal or perf_active:
        perf_lines.append(
            f"- 內科業績：人次 {c.visit_internal} → "
            f"獎金 **NT {c.bonus_internal:,} 元**"
        )
    if c.bonus_internal_combo or perf_active:
        combo_n = c.visit_internal_acu + c.visit_internal_trauma
        perf_lines.append(
            f"- 內針業績：人次 {combo_n}（內+針 {c.visit_internal_acu} + "
            f"內+傷 {c.visit_internal_trauma}）→ "
            f"獎金 **NT {c.bonus_internal_combo:,} 元**"
        )
    if c.bonus_pure_acu_trauma or perf_active:
        pure_n = c.visit_pure_acu + c.visit_pure_trauma
        perf_lines.append(
            f"- 針灸業績：人次 {pure_n}（純針 {c.visit_pure_acu} + "
            f"純傷 {c.visit_pure_trauma}）→ "
            f"獎金 **NT {c.bonus_pure_acu_trauma:,} 元**"
        )
    if perf_lines:
        header = (
            "**業績獎金** ✅" if perf_active
            else "**業績獎金**（平均人次 < 15.1，未觸發）"
        )
        L.append(header)
        L.extend(perf_lines)

    # 自費抽成 — 用 [銷售 × 比例 = 獎金] 格式
    bd = c.commission_breakdown or {}
    sales_revenue = sum(
        cash_row.get(k, 0) or 0 for k in
        ("internal_drug", "external_drug", "wellness", "herb_decoction")
    )
    # ⚠️ commission_breakdown 的 key 是英文（salary.py COMMISSION_FIELDS）
    sales_commission = sum(bd.get(k, 0) for k in
                           ("internal_drug", "external_drug", "wellness", "herb_decoction"))
    treatment_revenue = sum(
        cash_row.get(k, 0) or 0 for k in ("acupuncture", "trauma", "dislocation")
    )
    treatment_commission = sum(bd.get(k, 0) for k in ("acupuncture", "trauma", "dislocation"))
    other_revenue = cash_row.get("other", 0) or 0
    other_commission = bd.get("other", 0)
    lab_revenue = cash_row.get("lab", 0) or 0
    lab_commission = bd.get("lab", 0)
    consult_revenue = cash_row.get("consult", 0) or 0
    consult_commission = bd.get("consult", 0)

    cash_lines = []
    if sales_revenue:
        cash_lines.append(
            f"- 自費銷售業績（含減重）：[{sales_revenue:,} × 20% = "
            f"**{sales_commission:,}**] 元"
        )
    if treatment_revenue:
        cash_lines.append(
            f"- 自費療程業績：[{treatment_revenue:,} × 40% = "
            f"**{treatment_commission:,}**] 元"
        )
    if consult_revenue:
        rate_pct = "50%" if consult_commission else "0%"
        cash_lines.append(
            f"- 自費診察費：[{consult_revenue:,} × {rate_pct} = "
            f"**{consult_commission:,}**] 元"
        )
    if other_revenue:
        cash_lines.append(
            f"- 診斷證明（其它）：[{other_revenue:,} × 50% = "
            f"**{other_commission:,}**] 元"
        )
    if lab_revenue:
        cash_lines.append(
            f"- 三伏(九)貼（檢驗）：[{lab_revenue:,} × 10% = "
            f"**{lab_commission:,}**] 元"
        )
    if cash_lines:
        L.append(f"**自費抽成**（合計 **NT {c.commission_total:,} 元**）")
        L.extend(cash_lines)

    if c.acu_complex_bonus or c.a91_bonus:
        L.append("**A91+複針獎金**（115/04 起新制）")
        if c.acu_complex_mid_count or c.acu_complex_high_count:
            L.append(
                f"- 複針：[中 {c.acu_complex_mid_count} ×20 + 高 "
                f"{c.acu_complex_high_count} ×40 = "
                f"**{c.acu_complex_bonus:,}**] 元"
            )
        if c.a91_count:
            L.append(
                f"- A91 整合醫療：[{c.a91_count} 人 ×14 = "
                f"**{c.a91_bonus:,}**] 元"
            )

    L.append("---")
    L.append(f"**▶ 此診所應付：NT {c.gross:,} 元**")
    if labor_ded or nhi_ded:
        take = c.gross - labor_ded - nhi_ded
        L.append(f"勞保扣 NT {labor_ded:,}　|　健保扣 NT {nhi_ded:,}")
        L.append(f"**▶ 此診所實領：NT {take:,} 元**")

    if c.notes:
        L.append(f"⚠️ {chr(65307).join(c.notes) if False else '；'.join(c.notes)}")

    return L


def _render_payslip_block(c, cash_lookup, role_label,
                          labor_ded: int = 0, nhi_ded: int = 0):
    """渲染薪資單到 streamlit"""
    for line in _payslip_lines(c, cash_lookup, role_label, labor_ded, nhi_ded):
        st.markdown(line)


def _md_inline_to_html(s: str) -> str:
    """把 **bold** 轉 <b>bold</b>"""
    import re
    return re.sub(r"\*\*([^*]+?)\*\*", r"<b>\1</b>", s)


def _md_line_to_html(line: str) -> str:
    """單行 markdown → HTML"""
    if line.startswith("### "):
        return f"<h3>{_md_inline_to_html(line[4:])}</h3>"
    if line.startswith("## "):
        return f"<h2>{_md_inline_to_html(line[3:])}</h2>"
    if line == "---":
        return "<hr>"
    if line.startswith("- "):
        return f"<div style='margin-left:1.5em'>• {_md_inline_to_html(line[2:])}</div>"
    return f"<div>{_md_inline_to_html(line)}</div>"


def generate_doctor_payslip_html(
    comps, ps, cash_lookup: dict, role_label: dict, service_month: str,
    cash_payment: bool = False,
) -> str:
    """產生單一醫師薪資單的完整 HTML（給下載+瀏覽器列印用）。

    cash_payment=True：現金支付版 — 內容同一般版，末尾多
    「匯款金額（=當月投保額）／現金給付（=實領−匯款）／簽收欄」。
    """
    doctor_name = comps[0].doctor_name
    title = f"{doctor_name} 薪資單 {service_month[:7]}"
    if cash_payment:
        title += "（現金支付）"

    css = """
    <style>
    body { font-family: "Microsoft JhengHei", "PingFang TC", "Heiti TC", sans-serif;
           max-width: 1100px; margin: 30px auto; padding: 20px;
           color: #222; line-height: 1.6; }
    h1 { color: #6A5ACD; border-bottom: 2px solid #6A5ACD; padding-bottom: 8px; }
    h2 { color: #6A5ACD; }
    h3 { color: #444; margin-bottom: 6px; }
    .clinic-block { border-left: 4px solid #6A5ACD; padding: 8px 16px;
                    margin: 16px 0; background: #fafafa; border-radius: 4px; }
    .total { background: #f0eafc; padding: 16px; border-radius: 8px;
             margin-top: 24px; font-size: 17px; }
    .two-cols { display: flex; gap: 20px; flex-wrap: wrap; }
    .two-cols > div { flex: 1; min-width: 380px; }
    hr { border: none; border-top: 1px dashed #ccc; margin: 12px 0; }
    .cash-table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    .cash-table th, .cash-table td {
        border: 1.5px solid #6A5ACD; padding: 10px 14px; text-align: center; }
    .cash-table th.cash-label { background: #f0eafc; font-size: 15px;
        width: 130px; }
    .cash-table td.cash-amt { font-size: 20px; font-weight: bold;
        width: 220px; white-space: nowrap; }
    .remit-note { color: #666; font-size: 14px; }
    .sign-cell { height: 110px; vertical-align: bottom; text-align: left; }
    .sign-label { font-size: 11px; color: #999; font-weight: normal; }
    /* 強制列印背景色（瀏覽器預設不印背景，這裡覆蓋） */
    * { -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important; }
    @media print {
        @page { margin: 1.5cm; }
        body { margin: 0; }
    }
    </style>
    """

    # 現金支付版金額（v13 公式，院長 2026-09 裁定）：
    #   匯款金額 = 投保額 − 勞保扣 − 健保扣（玉山轉帳）
    #   現金給付 = 實領總額 − 匯款金額（由前月現金收入支付）
    remit = cash = 0
    cash_formula_lines: list[str] = []
    if cash_payment and ps:
        remit = max(
            int(ps.insurance_base or 0)
            - int(ps.labor_deduction or 0) - int(ps.nhi_deduction or 0),
            0,
        )
        cash = int(ps.take_home) - remit
        cash_formula_lines = [
            f"**▶ 匯款金額：投保額 {int(ps.insurance_base or 0):,} − "
            f"勞保扣 {int(ps.labor_deduction or 0):,} − "
            f"健保扣 {int(ps.nhi_deduction or 0):,} = {remit:,} 元**",
            f"**▶ 現金給付：{int(ps.take_home):,} − {remit:,} = {cash:,} 元**",
        ]

    html = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{title}</title>", css, "</head><body>",
        f"<h1>🩺 {doctor_name}　薪資單　{service_month[:7]}</h1>",
    ]

    if len(comps) > 1:
        comps_sorted = sorted(comps, key=lambda c: 0 if c.role != "support" else 1)
        html.append("<div class='two-cols'>")
        for c in comps_sorted:
            html.append("<div class='clinic-block'>")
            ld, nd = (
                (ps.labor_deduction, ps.nhi_deduction)
                if c.role != "support" and ps
                else (0, 0)
            )
            for line in _payslip_lines(c, cash_lookup, role_label, ld, nd):
                html.append(_md_line_to_html(line))
            if c.role != "support" and cash_formula_lines:
                for line in cash_formula_lines:
                    html.append(_md_line_to_html(line))
            html.append("</div>")
        html.append("</div>")
        if ps and ps.support_clinic_id:
            main_take = ps.gross_main - ps.labor_deduction - ps.nhi_deduction
            html.append("<div class='total'>")
            html.append("<h2>📊 兩診所合計</h2>")
            html.append(
                f"<div>{ps.main_clinic_name} 實領 NT {main_take:,} 元　＋　"
                f"{ps.support_clinic_name} 應付 NT {ps.gross_support:,} 元</div>"
            )
            html.append(
                f"<h2 style='color:#6A5ACD;margin-top:12px'>"
                f"實領總額：NT {ps.take_home:,} 元</h2>"
            )
            html.append("</div>")
    else:
        c = comps[0]
        html.append("<div class='clinic-block'>")
        ld, nd = (
            (ps.labor_deduction, ps.nhi_deduction)
            if c.role != "support" and ps
            else (0, 0)
        )
        for line in _payslip_lines(c, cash_lookup, role_label, ld, nd):
            html.append(_md_line_to_html(line))
        if c.role != "support" and cash_formula_lines:
            for line in cash_formula_lines:
                html.append(_md_line_to_html(line))
        html.append("</div>")

    # ─── 現金支付版：匯款/現金分 2 行（現金先發時只簽現金，匯款走帳簿）───
    if cash_payment and ps:
        cash_style = " style='color:#c0392b'" if cash < 0 else ""
        html.append("<div class='total'>")
        html.append("<h2>💵 給付方式</h2>")
        html.append("<table class='cash-table'>")
        html.append(
            "<tr><th class='cash-label'>匯款金額</th>"
            f"<td class='cash-amt'>NT {remit:,} 元</td>"
            "<td class='remit-note'>詳見匯款帳簿明細</td></tr>"
        )
        html.append(
            "<tr><th class='cash-label'>現金給付</th>"
            f"<td class='cash-amt'{cash_style}>NT {cash:,} 元</td>"
            "<td class='sign-cell'><span class='sign-label'>簽收</span></td></tr>"
        )
        html.append("</table>")
        html.append(
            "<div style='color:#777;font-size:12px;margin-top:8px'>"
            "匯款金額＝投保額 − 勞保扣 − 健保扣"
            f"（{int(ps.insurance_base or 0):,} − {int(ps.labor_deduction or 0):,}"
            f" − {int(ps.nhi_deduction or 0):,}）；"
            "現金給付＝實領總額 − 匯款金額"
            f"（NT {int(ps.take_home):,} − NT {remit:,}）</div>"
        )
        if cash < 0:
            html.append(
                "<div style='color:#c0392b;font-size:13px;margin-top:4px'>"
                "⚠️ 當月實領低於投保額，現金給付為負，請人工確認。</div>"
            )
        html.append("</div>")

    html.append(
        "<p style='text-align:center;color:#999;font-size:12px;margin-top:30px'>"
        f"由澤豐聯盟財務系統產出於 "
        f"{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p>"
    )
    html.append("</body></html>")
    return "\n".join(html)


def _visit_field(component, field_name: str) -> int:
    """從 SalaryComponent 取人次欄位（visit_stats 對應）"""
    mapping = {
        "nhi_internal": "visit_internal",
        "nhi_pure_acu": "visit_pure_acu",
        "nhi_pure_trauma": "visit_pure_trauma",
        "nhi_internal_acu": "visit_internal_acu",
        "nhi_internal_trauma": "visit_internal_trauma",
    }
    attr = mapping.get(field_name, field_name)
    return getattr(component, attr, 0) or 0


# ============================================================
# 5. 院長個人透支（Phase 5）
# ============================================================
def _prev_month_label(month_iso: str) -> str:
    """ISO YYYY-MM-01 → 前月 YYYY-MM 字串。"""
    from datetime import date
    d = date.fromisoformat(month_iso)
    if d.month == 1:
        return f"{d.year - 1:04d}-12"
    return f"{d.year:04d}-{d.month - 1:02d}"


def page_personal():
    st.title("💸 院長個人財富分析")

    from data_processor.personal_finance import (
        calculate_zhou_monthly, list_available_months,
    )
    import altair as alt

    st.caption(
        "🧾 **反推法**：澤豐&個人中信戶混合診所/院長/澤沛代墊，"
        "用 期初餘額+流入-已知診所流出-期末餘額 反推院長個人提領。"
        "輔以澤豐玉山轉到院長個人帳號(n2)。"
    )

    sb = get_authed_client()
    months = list_available_months(sb)
    if not months:
        st.warning("⚠️ 尚無中信交易資料")
        return

    col1, col2 = st.columns([2, 5])
    with col1:
        sel_month = st.selectbox(
            "月份", months, format_func=lambda d: d[:7],
            key="personal_month",
        )

    with st.spinner("計算中..."):
        try:
            z = calculate_zhou_monthly(sb, sel_month)
        except Exception as e:
            st.error(f"計算失敗：{e}")
            return

    # ─── KPI ───
    st.divider()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("周院長收入 (x13)", f"NT$ {z.x13_zhou_salary:,}")
    k2.metric("總支出 N (n1+n2−x2')", f"NT$ {z.total_expense:,}")
    k3.metric("私人支出 (N−支票)", f"NT$ {z.private_expense:,}")
    k4.metric("透支 (花到診所營收)",
              f"NT$ {z.overdraft:,}",
              delta=("⚠️ 超支" if z.overdraft > 0 else "✅ 結餘"),
              delta_color="inverse")

    # ─── 1. 周院長收入 (x13) ───
    st.divider()
    st.subheader("① 周院長收入 (x13 周明毅看診薪資，前月服務薪資本月實領)")
    if z.x13_items:
        df = pd.DataFrame(z.x13_items)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown(f"**合計 NT$ {z.x13_zhou_salary:,}**")
        st.caption(
            f"（顯示 {sel_month[:7]} 帳列為前月 service_month 之兩院 total_salary 加總）"
        )
    else:
        st.info(
            f"前月（{_prev_month_label(sel_month)}）"
            f"doctor_salary_monthly 無 周明毅 資料 — 請先到「💵 醫師薪資」頁計算寫入"
        )

    # ─── 2. 周院長支出（含支票）N = n1 + n2 ───
    st.divider()
    st.subheader("② 周院長支出（含支票）N = n1 + n2")
    st.markdown(
        f"### 🏦 n1（澤豐中信戶）= **NT$ {z.n1:,}**"
    )
    n1_rows = [
        ("x1   N 月初餘額", z.x1_prev_balance, "+"),
        ("x2   健保戶→中信 轉入", z.x2_clinic_transfer_in, "+"),
        ("x2'  中信→玉山 院長補貼 (僅顯示)",
         z.x2_personal_subsidy_out, "（隱含於 n1，在總和處扣回）"),
        ("x3   澤豐現金支出", z.x3_zefeng_cash_expense, "−"),
        ("x4   澤沛現金支出代墊（N+1 反推）", z.x4_zepei_cash_advance, "−"),
        ("x5   澤沛還現金代墊（前月）", z.x5_zepei_cash_repay, "+"),
        ("x6   豐沛金流入帳", z.x6_fengpei_in, "+"),
        ("x7   澤沛合約還款入帳", z.x7_zepei_contract_in, "+"),
        ("x8   澤豐現金存入", z.x8_zefeng_cash_in, "+"),
        ("x9   編制外人力薪資（謝松坊）", z.x9_external_staff_salary, "−"),
        ("x10  手KEY 非常規 net", z.x10_manual_net, "+"),
        ("x12  澤豐合約支出", z.x12_zefeng_contract_expense, "−"),
        ("x11  N 月底餘額", z.x11_current_balance, "−"),
    ]
    n1_df = pd.DataFrame(
        [{"變數": k, "金額": v, "符號": s} for k, v, s in n1_rows]
    )
    n1_df["金額"] = n1_df["金額"].apply(lambda v: f"{v:,}")
    st.dataframe(n1_df, use_container_width=True, hide_index=True)

    st.markdown(
        f"### 🏦 n2（澤豐玉山→院長個人帳號）= **NT$ {z.n2:,}**"
    )
    if z.n2_items:
        st.dataframe(
            pd.DataFrame(z.n2_items),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("該月無玉山→院長個人帳號的轉出")

    st.markdown(
        f"### 💸 **N 總支出 = n1 + n2 − x2'** "
        f"= {z.n1:,} + {z.n2:,} − {z.x2_personal_subsidy_out:,} "
        f"= **NT$ {z.total_expense:,}**"
    )
    st.caption(
        "x2' 僅為「中信→澤豐玉山健保戶」(末段 0347940007803) 的院長補貼診所。"
        "「中信→院長個人玉山」(末段 0668979072975) 是院長自己帳戶間移轉，"
        "仍留在 n1，未額外扣除。"
    )

    # ─── 3. 周院長私人支出 = N - 支票 ───
    st.divider()
    st.subheader("③ 周院長私人支出 = N − 支票支出 P")
    st.markdown(
        f"#### 🎫 支票支出 P = **NT$ {z.p_check_total:,}**　"
        "（兩家銀行支票總額）"
    )
    if z.p_check_items:
        st.dataframe(
            pd.DataFrame(z.p_check_items),
            use_container_width=True, hide_index=True,
        )
    st.markdown(
        f"#### ✅ 周院長私人支出 = "
        f"{z.total_expense:,} − {z.p_check_total:,} = "
        f"**NT$ {z.private_expense:,}**"
    )

    # ─── 4. 周院長支出手KEY紀錄 ───
    st.divider()
    st.subheader("④ 周院長支出手 KEY 紀錄（屬性=個人 + 分類=只記帳）")
    if z.personal_memo_items:
        df = pd.DataFrame(z.personal_memo_items)
        st.dataframe(df, use_container_width=True, hide_index=True)
        total_memo = sum(int(it.get("amount") or 0) for it in z.personal_memo_items)
        st.caption(f"合計 NT$ {total_memo:,}（僅為大筆開支備註，不入 N 計算）")
    else:
        st.info(
            "尚無紀錄。需於「📥 本月資料匯入區 → 📝 手 KEY：金流補充備註」"
            "新增屬性=個人、分類=🟡 只記帳 的條目"
        )

    # ─── 5. 周院長透支 ───
    st.divider()
    st.subheader("⑤ 周院長透支（花費到澤豐的營收）")
    st.markdown(
        f"#### 透支 = 私人支出 − 院長收入 = "
        f"{z.private_expense:,} − {z.x13_zhou_salary:,} = "
        f"**NT$ {z.overdraft:,}**"
    )
    if z.overdraft > 0:
        st.error(
            f"⚠️ 本月院長私人支出超過薪資收入 NT$ {z.overdraft:,}，"
            "等同動用診所盈餘"
        )
    else:
        st.success(f"✅ 本月私人收支結餘 NT$ {-z.overdraft:,}")

    # ─── 趨勢圖（近 12 月） ───
    st.divider()
    st.subheader("📊 近 12 個月趨勢")
    chart_months = months[:min(12, len(months))]
    trend_rows = []
    with st.spinner("計算近 12 個月..."):
        for sm in chart_months:
            try:
                if sm == sel_month:
                    zz = z  # 重用
                else:
                    zz = calculate_zhou_monthly(sb, sm)
                # 完整門檻：x13 > 0 且 x11 > 0
                if zz.x13_zhou_salary <= 0 or zz.x11_current_balance <= 0:
                    continue
                trend_rows.append({
                    "月份": sm[:7],
                    "收入 (x13)": zz.x13_zhou_salary,
                    "私人支出": zz.private_expense,
                    "透支": zz.overdraft,
                })
            except Exception:
                continue

    if not trend_rows:
        st.info("近 12 個月內無資料完整的月份")
    else:
        tdf = pd.DataFrame(trend_rows).sort_values("月份")
        long = tdf.melt(id_vars=["月份"], var_name="項目", value_name="金額")
        chart = alt.Chart(long).mark_bar(size=18).encode(
            x=alt.X("月份:N", sort="ascending"),
            y=alt.Y("金額:Q", title="NT$"),
            color=alt.Color(
                "項目:N",
                scale=alt.Scale(
                    domain=["收入 (x13)", "私人支出", "透支"],
                    range=["#5CB85C", "#4A90E2", "#D9534F"],
                ),
            ),
            xOffset="項目:N",
            tooltip=["月份", "項目", alt.Tooltip("金額:Q", format=",")],
        ).properties(height=340)
        st.altair_chart(chart, use_container_width=True)


# ============================================================
# 5. 系統設定
# ============================================================
def page_settings():
    st.title("⚙️ 系統設定")

    sb = get_authed_client()

    tab1, tab2, tab_ins, tab_cost, tab_pl, tab3 = st.tabs(
        ["白名單使用者", "醫師主檔", "勞健保扣除額", "成本參數",
         "損益分析名單", "系統資訊"]
    )

    with tab1:
        st.subheader("授權使用者列表")
        try:
            users = sb.table("allowed_users").select("*").execute()
            if users.data:
                st.dataframe(pd.DataFrame(users.data), use_container_width=True)
            else:
                st.info("尚無授權使用者。新增需在 Supabase 後台手動 INSERT。")
        except Exception as e:
            st.error(f"讀取失敗：{e}")

    with tab2:
        _settings_doctor_roster(sb)

    with tab_ins:
        _settings_insurance_deductions(sb)

    with tab_cost:
        _settings_cost_params(sb)

    with tab_pl:
        _settings_pl_lists(sb)

    with tab3:
        st.subheader("系統資訊")
        st.text(f"登入者：{st.session_state.session.get('email')}")
        st.text(f"角色：{st.session_state.get('user_role', {}).get('role', 'unknown')}")
        st.text(f"User ID：{st.session_state.session.get('user_id')}")
        st.caption("Supabase URL：" + st.secrets["supabase"]["url"])


def _settings_doctor_roster(sb):
    """醫師-診所角色配置（v12 日期版本化：到職/離職/轉診所/支援/調參數）"""
    from datetime import date, timedelta
    from data_processor.doctor_config import (
        fetch_doctor_clinic, fetch_session_fees,
        doctor_employment_status, parse_date, previous_day,
    )

    st.subheader("醫師-診所角色配置")
    st.caption(
        "所有角色與參數都帶**生效區間**：薪資計算只取「該月有效」的角色列，"
        "診薪/院長津貼取「該月月底有效」的版本計整月（**調參數請以月初為生效日**）。"
        "修改只影響生效日之後的月份，之前月份重算不受影響。"
        "新增醫師後，各上傳檔案（門診申報/看診人數/自費統計…）內的同名醫師即可被辨識。"
    )

    today = date.today()
    role_label = {"director": "負責醫", "regular": "執業醫", "support": "支援醫"}

    try:
        doctors = sb.table("doctors").select("id, name, session_fee, is_active").execute().data
        clinics = sb.table("clinics").select("id, short_name").execute().data
        dc_rows = fetch_doctor_clinic(sb)
        fee_rows = fetch_session_fees(sb)
    except Exception as e:
        st.error(f"讀取失敗：{e}")
        return

    migrated = (not dc_rows) or ("effective_from" in dc_rows[0])
    if not migrated:
        st.warning(
            "⚠️ 資料庫尚未執行 **migration_v11_to_v12.sql**（doctor_clinic 生效日期欄位不存在），"
            "以下僅供唯讀檢視。請先到 Supabase SQL Editor 執行 migration 再回來編輯。"
        )

    did_name = {d["id"]: d["name"] for d in doctors}
    cid_name = {c["id"]: c["short_name"] for c in clinics}

    def _current_fee(did: int):
        """今日有效診薪（歷史列優先，無則 doctors.session_fee）"""
        best, best_from = None, None
        for r in fee_rows:
            if r["doctor_id"] != did:
                continue
            f = parse_date(r.get("effective_from")) or date.min
            if f > today:
                continue
            if best_from is None or f > best_from:
                best_from, best = f, r
        if best is not None:
            return float(best["session_fee"])
        d = next((x for x in doctors if x["id"] == did), None)
        return float(d["session_fee"] or 0) if d else 0.0

    status = doctor_employment_status(dc_rows, today)

    # ─── 主區：現行配置（今日有效 + 未來生效）───
    def _fmt_range(r) -> tuple[str, str]:
        f = r.get("effective_from") or ""
        t = r.get("effective_to") or ""
        return str(f)[:10], str(t)[:10]

    current_view, history_view = [], []
    for r in dc_rows:
        ef_to = parse_date(r.get("effective_to"))
        f_str, t_str = _fmt_range(r)
        ef_from = parse_date(r.get("effective_from"))
        row_disp = {
            "醫師": did_name.get(r["doctor_id"], "?"),
            "診所": cid_name.get(r["clinic_id"], "?"),
            "角色": role_label.get(r["role"], r["role"]),
            "院長津貼": r.get("director_allowance") or 0,
            "現行診薪": _current_fee(r["doctor_id"]),
            "生效起": f_str or "（初始）",
            "結束": t_str or "—",
            "備註": r.get("note") or "",
        }
        if ef_to is None or ef_to >= today:
            if ef_from and ef_from > today:
                row_disp["角色"] += "（未來生效）"
            current_view.append(row_disp)
        else:
            history_view.append((r, row_disp))

    if current_view:
        st.markdown("**📋 現行在職配置**")
        st.dataframe(
            pd.DataFrame(current_view), use_container_width=True, hide_index=True
        )
    else:
        st.info("目前沒有有效的角色配置。")

    # ─── 近期離職（結束未滿 1 年）───
    recent_resigned = {
        did: s["last_end"] for did, s in status.items()
        if not s["active"] and s["last_end"]
        and (today - s["last_end"]).days <= 365
    }
    if recent_resigned:
        st.markdown("**👋 近期離職（未滿 1 年）**")
        st.dataframe(
            pd.DataFrame([
                {"醫師": did_name.get(did, "?"), "離職日": str(end)}
                for did, end in sorted(recent_resigned.items(), key=lambda kv: kv[1], reverse=True)
            ]),
            use_container_width=True, hide_index=True,
        )

    # ─── 歷史紀錄（自動收合：離職滿 1 年 / 調薪滿 2 年預設隱藏）───
    superseded_fees = [
        r for r in fee_rows
        if parse_date(r.get("effective_to")) is not None
        or any(
            r2["doctor_id"] == r["doctor_id"]
            and (parse_date(r2.get("effective_from")) or date.min)
            > (parse_date(r.get("effective_from")) or date.min)
            for r2 in fee_rows
        )
    ]
    if history_view or superseded_fees:
        with st.expander("🗂️ 歷史紀錄（已結束角色、舊參數版本）"):
            show_all = st.checkbox(
                "顯示全部（含離職滿 1 年、調薪滿 2 年的更早紀錄）",
                key="roster_show_all_history",
            )
            one_year_ago = today - timedelta(days=365)
            two_years_ago = today - timedelta(days=730)

            hist_rows = []
            for r, disp in history_view:
                ef_to = parse_date(r.get("effective_to"))
                resigned_long = (
                    not status.get(r["doctor_id"], {}).get("active", True)
                    and status[r["doctor_id"]]["last_end"]
                    and status[r["doctor_id"]]["last_end"] < one_year_ago
                )
                if not show_all and (resigned_long or (ef_to and ef_to < two_years_ago)):
                    continue
                hist_rows.append(disp)
            if hist_rows:
                st.markdown("**已結束的角色列**")
                st.dataframe(pd.DataFrame(hist_rows),
                             use_container_width=True, hide_index=True)

            fee_hist = []
            for r in sorted(
                superseded_fees,
                key=lambda x: (x["doctor_id"], str(x.get("effective_from") or "")),
                reverse=True,
            ):
                ef_to = parse_date(r.get("effective_to"))
                if not show_all and ef_to and ef_to < two_years_ago:
                    continue
                fee_hist.append({
                    "醫師": did_name.get(r["doctor_id"], "?"),
                    "診薪": float(r["session_fee"]),
                    "生效起": str(r.get("effective_from") or "")[:10],
                    "結束": str(r.get("effective_to") or "")[:10] or "—",
                    "備註": r.get("note") or "",
                })
            if fee_hist:
                st.markdown("**診薪調整歷史**")
                st.dataframe(pd.DataFrame(fee_hist),
                             use_container_width=True, hide_index=True)
            if not hist_rows and not fee_hist:
                st.caption("（更早的紀錄已隱藏，勾選上方「顯示全部」查看）")

    # ─── 編輯區 ───
    if not st.session_state.get("edit_mode"):
        st.info("⚠️ 唯讀模式。如需異動配置，請啟用左下「編輯模式」。")
        return
    if not migrated:
        return

    st.divider()
    action = st.selectbox(
        "操作類型",
        [
            "➕ 新增醫師（到職）",
            "🤝 新增支援角色（跨院兼診）",
            "🔚 結束單一角色（如：結束某院支援）",
            "🔁 轉換診所（換主聘）",
            "📅 醫師離職",
            "💲 調整診薪",
            "💰 調整院長津貼",
            "✏️ 進階：直接編修單列",
        ],
        key="roster_action",
    )

    active_dc = [
        r for r in dc_rows
        if parse_date(r.get("effective_to")) is None
        or parse_date(r["effective_to"]) >= today
    ]

    def _end_row(row_id: int, end_date: date, note_append: str):
        old = next(r for r in dc_rows if r.get("id") == row_id)
        new_note = ((old.get("note") or "") + f"；{note_append}").lstrip("；")
        sb.table("doctor_clinic").update({
            "effective_to": str(end_date), "note": new_note,
        }).eq("id", row_id).execute()

    # 1) 新增醫師 ------------------------------------------------------------
    if action.startswith("➕ 新增醫師"):
        col1, col2 = st.columns(2)
        with col1:
            new_name = st.text_input("醫師姓名（與報表檔案內名稱一致）", key="ro_new_name")
            new_clinic = st.selectbox("主聘診所", list(cid_name), format_func=cid_name.get, key="ro_new_clinic")
            new_role = st.selectbox("角色", ["regular", "director"],
                                    format_func=role_label.get, key="ro_new_role")
        with col2:
            new_start = st.date_input("到職日", value=today, key="ro_new_start")
            new_fee = st.number_input("診薪（每診）", min_value=0.0, step=0.1,
                                      value=0.0, format="%.1f", key="ro_new_fee")
            new_allow = st.number_input("院長津貼（負責醫才有）", min_value=0,
                                        step=1000, value=0, key="ro_new_allow")
        st.caption("到職後如有投保，請到「勞健保扣除額」分頁新增該醫師的扣除配置（生效日=投保日）。")
        if st.button("💾 建立醫師", type="primary", key="ro_new_save"):
            name = (new_name or "").strip()
            if not name:
                st.error("請輸入姓名")
            else:
                try:
                    exist = next((d for d in doctors if d["name"] == name), None)
                    if exist:
                        did = exist["id"]
                        sb.table("doctors").update(
                            {"session_fee": new_fee, "is_active": True}
                        ).eq("id", did).execute()
                    else:
                        did = sb.table("doctors").insert(
                            {"name": name, "session_fee": new_fee, "is_active": True}
                        ).execute().data[0]["id"]
                    dup = [r for r in active_dc
                           if r["doctor_id"] == did and r["clinic_id"] == new_clinic]
                    if dup:
                        st.error(f"{name} 在 {cid_name[new_clinic]} 已有未結束的角色列，請改用其他操作。")
                    else:
                        sb.table("doctor_clinic").insert({
                            "doctor_id": did, "clinic_id": new_clinic,
                            "role": new_role,
                            "director_allowance": new_allow if new_role == "director" else 0,
                            "effective_from": str(new_start),
                            "note": f"到職（{new_start}）",
                        }).execute()
                        sb.table("doctor_session_fees").upsert({
                            "doctor_id": did, "session_fee": new_fee,
                            "effective_from": str(new_start),
                            "note": f"到職（{new_start}）",
                        }, on_conflict="doctor_id,effective_from").execute()
                        st.success(f"✅ 已建立 {name}（{cid_name[new_clinic]} {role_label[new_role]}，{new_start} 起）")
                        st.rerun()
                except Exception as e:
                    st.error(f"建立失敗：{e}")

    # 2) 新增支援角色 --------------------------------------------------------
    elif action.startswith("🤝"):
        col1, col2 = st.columns(2)
        with col1:
            sup_doc = st.selectbox("醫師", list(did_name), format_func=did_name.get, key="ro_sup_doc")
            sup_clinic = st.selectbox("支援診所", list(cid_name), format_func=cid_name.get, key="ro_sup_clinic")
        with col2:
            sup_start = st.date_input("支援起始日", value=today, key="ro_sup_start")
        if st.button("💾 新增支援角色", type="primary", key="ro_sup_save"):
            dup = [r for r in active_dc
                   if r["doctor_id"] == sup_doc and r["clinic_id"] == sup_clinic]
            if dup:
                st.error(f"{did_name[sup_doc]} 在 {cid_name[sup_clinic]} 已有未結束的角色列。")
            else:
                try:
                    sb.table("doctor_clinic").insert({
                        "doctor_id": sup_doc, "clinic_id": sup_clinic,
                        "role": "support", "director_allowance": 0,
                        "effective_from": str(sup_start),
                        "note": f"支援起始（{sup_start}）",
                    }).execute()
                    st.success(f"✅ {did_name[sup_doc]} 自 {sup_start} 起支援 {cid_name[sup_clinic]}")
                    st.rerun()
                except Exception as e:
                    st.error(f"新增失敗：{e}")

    # 2b) 結束單一角色 -------------------------------------------------------
    elif action.startswith("🔚"):
        st.caption(
            "只結束選定的那一條角色列（其他診所的角色不動）。"
            "例：A 醫師結束澤沛支援、但續任澤豐主聘 → 選澤沛支援列。"
            "整個人離職請用「醫師離職」。"
        )
        if not active_dc:
            st.info("沒有現行角色列。")
        else:
            opt = st.selectbox(
                "要結束的角色列",
                active_dc,
                format_func=lambda r: (
                    f"{did_name.get(r['doctor_id'])}｜{cid_name.get(r['clinic_id'])}"
                    f"｜{role_label.get(r['role'], r['role'])}"
                    f"｜{str(r.get('effective_from') or '')[:10]} 起"
                ),
                key="ro_end_row",
            )
            end_date = st.date_input("最後有效日（含）", value=today, key="ro_end_date")
            if st.button("💾 結束此角色", type="primary", key="ro_end_save"):
                try:
                    _end_row(opt["id"], end_date, f"角色結束（{end_date}）")
                    st.success(
                        f"✅ {did_name[opt['doctor_id']]} 於 {cid_name[opt['clinic_id']]} 的"
                        f"{role_label.get(opt['role'])}角色已於 {end_date} 結束"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"結束失敗：{e}")

    # 3) 轉換診所 ------------------------------------------------------------
    elif action.startswith("🔁"):
        mains = [r for r in active_dc if r["role"] != "support"]
        if not mains:
            st.info("沒有可轉換的現行主聘角色列。")
        else:
            opt = st.selectbox(
                "現行主聘（轉出）",
                mains,
                format_func=lambda r: (
                    f"{did_name.get(r['doctor_id'])}｜{cid_name.get(r['clinic_id'])}"
                    f"｜{role_label.get(r['role'])}"
                ),
                key="ro_tr_row",
            )
            col1, col2 = st.columns(2)
            with col1:
                tr_clinic = st.selectbox(
                    "轉入診所",
                    [c for c in cid_name if c != opt["clinic_id"]],
                    format_func=cid_name.get, key="ro_tr_clinic",
                )
                tr_role = st.selectbox("轉入角色", ["director", "regular"],
                                       format_func=role_label.get, key="ro_tr_role")
            with col2:
                tr_date = st.date_input("轉換生效日（新診所第一天）", value=today, key="ro_tr_date")
                tr_allow = st.number_input("轉入院長津貼", min_value=0, step=1000,
                                           value=0, key="ro_tr_allow")
            keep_support = st.checkbox(
                f"原診所（{cid_name.get(opt['clinic_id'])}）自生效日起續任支援醫",
                key="ro_tr_keep_support",
            )
            # 目標診所若已有未結束的支援列（原本就在該院兼診）→ 自動升主聘
            old_support = [
                r for r in active_dc
                if r["doctor_id"] == opt["doctor_id"]
                and r["clinic_id"] == tr_clinic and r["role"] == "support"
            ]
            if old_support:
                st.caption(
                    f"ℹ️ 偵測到其在 {cid_name[tr_clinic]} 的支援列，"
                    "將自動於生效日前一天結束（升為主聘）。"
                )
            st.caption(
                "原診所主聘列自動於生效日前一天結束。"
                "勞健保若隨主聘診所轉移，請至「勞健保扣除額」分頁登記異動"
                "（舊診所登記全 0、新診所登記新狀態，生效日=轉換日）。"
            )
            if st.button("💾 執行轉換", type="primary", key="ro_tr_save"):
                try:
                    _end_row(opt["id"], previous_day(tr_date), f"轉出至{cid_name[tr_clinic]}（{tr_date} 生效）")
                    for r in old_support:
                        _end_row(r["id"], previous_day(tr_date), f"升為主聘（{tr_date}）")
                    sb.table("doctor_clinic").insert({
                        "doctor_id": opt["doctor_id"], "clinic_id": tr_clinic,
                        "role": tr_role,
                        "director_allowance": tr_allow if tr_role == "director" else 0,
                        "effective_from": str(tr_date),
                        "note": f"自{cid_name.get(opt['clinic_id'])}轉入（{tr_date}）",
                    }).execute()
                    if keep_support:
                        sb.table("doctor_clinic").insert({
                            "doctor_id": opt["doctor_id"],
                            "clinic_id": opt["clinic_id"],
                            "role": "support", "director_allowance": 0,
                            "effective_from": str(tr_date),
                            "note": f"轉主聘後續任支援（{tr_date}）",
                        }).execute()
                    msg = (
                        f"✅ {did_name[opt['doctor_id']]} 自 {tr_date} 起轉至 "
                        f"{cid_name[tr_clinic]}（{role_label[tr_role]}）"
                    )
                    if old_support:
                        msg += f"；原 {cid_name[tr_clinic]} 支援列已結束"
                    if keep_support:
                        msg += f"；{cid_name.get(opt['clinic_id'])} 續任支援"
                    st.success(msg)
                    st.rerun()
                except Exception as e:
                    st.error(f"轉換失敗：{e}")

    # 4) 醫師離職 ------------------------------------------------------------
    elif action.startswith("📅"):
        active_dids = sorted(
            {r["doctor_id"] for r in active_dc},
            key=lambda d: did_name.get(d, ""),
        )
        if not active_dids:
            st.info("沒有在職醫師。")
        else:
            col1, col2 = st.columns(2)
            with col1:
                rs_doc = st.selectbox("離職醫師", active_dids,
                                      format_func=did_name.get, key="ro_rs_doc")
            with col2:
                rs_date = st.date_input("最後在職日", value=today, key="ro_rs_date")
            affected = [r for r in active_dc if r["doctor_id"] == rs_doc]
            st.caption(
                "將結束該醫師所有未結束的角色列（"
                + "、".join(f"{cid_name.get(r['clinic_id'])} {role_label.get(r['role'])}" for r in affected)
                + "），並自動補一筆勞健保**全 0 異動**（生效日=離職次日，"
                "歷史保留、離職前月份重算不受影響）。"
                "離職後月份不再列入薪資計算；該醫師會收合到勞健保頁「已無投保」區。"
            )
            if st.button("💾 確認離職", type="primary", key="ro_rs_save"):
                try:
                    from data_processor.doctor_config import (
                        current_insurance_snapshot,
                    )
                    for r in affected:
                        _end_row(r["id"], rs_date, f"離職（{rs_date}）")
                    # 勞健保退保：補全 0 異動（快照模型，不刪歷史）
                    ins_rows = sb.table("doctor_insurance_deductions").select(
                        "id, clinic_id, doctor_id, insurance_base, "
                        "labor_deduction, nhi_deduction, effective_from, effective_to"
                    ).eq("doctor_id", rs_doc).execute().data
                    day_after = rs_date + timedelta(days=1)
                    for c_id in {r["clinic_id"] for r in ins_rows}:
                        snap = current_insurance_snapshot(
                            ins_rows, c_id, rs_doc, rs_date
                        )
                        if snap["labor"] == 0 and snap["nhi"] == 0:
                            continue
                        sb.table("doctor_insurance_deductions").upsert({
                            "clinic_id": c_id, "doctor_id": rs_doc,
                            "insurance_base": 0, "labor_deduction": 0,
                            "nhi_deduction": 0,
                            "effective_from": str(day_after),
                            "effective_to": None,
                            "note": f"離職退保（最後在職日 {rs_date}）",
                        }, on_conflict="clinic_id,doctor_id,effective_from").execute()
                    st.success(f"✅ {did_name[rs_doc]} 已登記離職（最後在職日 {rs_date}）")
                    st.rerun()
                except Exception as e:
                    st.error(f"登記失敗：{e}")

    # 5) 調整診薪 ------------------------------------------------------------
    elif action.startswith("💲"):
        col1, col2 = st.columns(2)
        with col1:
            fee_doc = st.selectbox("醫師", list(did_name),
                                   format_func=did_name.get, key="ro_fee_doc")
            st.metric("現行診薪", f"{_current_fee(fee_doc):,.1f}")
        with col2:
            next_month_first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            fee_date = st.date_input("生效日（建議月初）", value=next_month_first, key="ro_fee_date")
            fee_new = st.number_input("新診薪", min_value=0.0, step=0.1,
                                      value=float(_current_fee(fee_doc)),
                                      format="%.1f", key="ro_fee_new")
        if fee_date.day != 1:
            st.warning("生效日不是月初：薪資按「月底有效值」計整月，該月整月都會用新診薪。")
        if st.button("💾 調整診薪", type="primary", key="ro_fee_save"):
            try:
                open_rows = [
                    r for r in fee_rows
                    if r["doctor_id"] == fee_doc
                    and parse_date(r.get("effective_to")) is None
                ]
                for r in open_rows:
                    sb.table("doctor_session_fees").update(
                        {"effective_to": str(previous_day(fee_date))}
                    ).eq("id", r["id"]).execute()
                sb.table("doctor_session_fees").upsert({
                    "doctor_id": fee_doc, "session_fee": fee_new,
                    "effective_from": str(fee_date),
                    "note": f"調薪（{fee_date} 起）",
                }, on_conflict="doctor_id,effective_from").execute()
                sb.table("doctors").update(
                    {"session_fee": fee_new}
                ).eq("id", fee_doc).execute()
                st.success(f"✅ {did_name[fee_doc]} 自 {fee_date} 起診薪 {fee_new:,.1f}")
                st.rerun()
            except Exception as e:
                st.error(f"調整失敗：{e}")

    # 6) 調整院長津貼 --------------------------------------------------------
    elif action.startswith("💰"):
        allow_rows = [r for r in active_dc if r["role"] != "support"]
        if not allow_rows:
            st.info("沒有現行主聘角色列。")
        else:
            opt = st.selectbox(
                "醫師×診所",
                allow_rows,
                format_func=lambda r: (
                    f"{did_name.get(r['doctor_id'])}｜{cid_name.get(r['clinic_id'])}"
                    f"｜現行津貼 {r.get('director_allowance') or 0:,}"
                ),
                key="ro_al_row",
            )
            col1, col2 = st.columns(2)
            with col1:
                next_month_first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
                al_date = st.date_input("生效日（建議月初）", value=next_month_first, key="ro_al_date")
            with col2:
                al_new = st.number_input("新院長津貼", min_value=0, step=1000,
                                         value=int(opt.get("director_allowance") or 0),
                                         key="ro_al_new")
            if al_date.day != 1:
                st.warning("生效日不是月初：薪資按「月底有效值」計整月，該月整月都會用新津貼。")
            if st.button("💾 調整津貼", type="primary", key="ro_al_save"):
                try:
                    _end_row(opt["id"], previous_day(al_date), f"津貼調整（{al_date} 起 {al_new:,}）")
                    sb.table("doctor_clinic").insert({
                        "doctor_id": opt["doctor_id"], "clinic_id": opt["clinic_id"],
                        "role": opt["role"], "director_allowance": al_new,
                        "effective_from": str(al_date),
                        "note": f"津貼調整（{al_date} 起）",
                    }).execute()
                    st.success(
                        f"✅ {did_name[opt['doctor_id']]}（{cid_name[opt['clinic_id']]}）"
                        f"自 {al_date} 起院長津貼 {al_new:,}"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"調整失敗：{e}")

    # 7) 進階編修 ------------------------------------------------------------
    else:
        st.caption("直接編修單列（打錯資料時用）。一般異動請用上面的專用操作，才會自動維持版本鏈。")
        rows_with_id = [r for r in dc_rows if r.get("id") is not None]
        if not rows_with_id:
            st.info("沒有可編修的角色列。")
            return
        opt = st.selectbox(
            "角色列",
            rows_with_id,
            format_func=lambda r: (
                f"id={r['id']}｜{did_name.get(r['doctor_id'])}｜{cid_name.get(r['clinic_id'])}"
                f"｜{role_label.get(r['role'], r['role'])}"
                f"｜{str(r.get('effective_from') or '')[:10]}"
                f"～{str(r.get('effective_to') or '')[:10] or '現行'}"
            ),
            key="ro_adv_row",
        )
        col1, col2 = st.columns(2)
        with col1:
            adv_role = st.selectbox(
                "角色", ["director", "regular", "support"],
                index=["director", "regular", "support"].index(opt["role"]),
                format_func=role_label.get, key="ro_adv_role",
            )
            adv_allow = st.number_input(
                "院長津貼", min_value=0, step=1000,
                value=int(opt.get("director_allowance") or 0), key="ro_adv_allow",
            )
        with col2:
            adv_from = st.date_input(
                "生效起", value=parse_date(opt.get("effective_from")) or today,
                key="ro_adv_from",
            )
            adv_to = st.date_input(
                "結束（留空=現行有效）",
                value=parse_date(opt.get("effective_to")), key="ro_adv_to",
            )
        adv_note = st.text_input("備註", value=opt.get("note") or "", key="ro_adv_note")
        col_s, col_d = st.columns(2)
        with col_s:
            if st.button("💾 儲存", type="primary", key="ro_adv_save"):
                try:
                    sb.table("doctor_clinic").update({
                        "role": adv_role, "director_allowance": adv_allow,
                        "effective_from": str(adv_from),
                        "effective_to": str(adv_to) if adv_to else None,
                        "note": adv_note or None,
                    }).eq("id", opt["id"]).execute()
                    st.success(f"✅ 已更新 id={opt['id']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"更新失敗：{e}")
        with col_d:
            if st.button("🗑️ 刪除此列", key="ro_adv_del"):
                try:
                    sb.table("doctor_clinic").delete().eq("id", opt["id"]).execute()
                    st.success(f"✅ 已刪除 id={opt['id']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"刪除失敗：{e}")


def _settings_insurance_deductions(sb):
    """勞健保投保異動管理（v12.1 快照模型：每筆=異動生效日起的完整狀態）"""
    from datetime import date as _date
    from data_processor.doctor_config import (
        current_insurance_snapshot, insurance_for_month,
    )

    st.subheader("勞健保扣除額（投保異動）")
    st.caption(
        "每筆紀錄＝一次**投保異動**：填「異動生效日」＋當日起的完整狀態"
        "（勞保扣／健保扣／投保額三欄都要確實填寫，未投保填 **0**），"
        "有效至下一筆異動的前一天，**不需退保日**——退保＝再登記一筆、把該欄改 0。"
        "勞健保可分開投保：如到職先只保健保（勞保填 0），一個月後加勞保再登記一筆。\n\n"
        "計算：**勞保**按日比例 = 扣除額/30 × 當月在保天數（整月在保扣全額，"
        "月中異動自動切段）；**健保**當月任一天在保即扣全額。"
        "只在主聘診所扣一次，支援診所扣 0。"
        "離職請用「醫師主檔 → 醫師離職」，會自動補一筆全 0 異動。"
    )

    try:
        rows = sb.table("doctor_insurance_deductions").select(
            "id, clinic_id, doctor_id, insurance_base, "
            "labor_deduction, nhi_deduction, effective_from, effective_to, note"
        ).execute().data
        clinics = {c["id"]: c["short_name"]
                   for c in sb.table("clinics").select("id, short_name").execute().data}
        doctors = {d["id"]: d["name"]
                   for d in sb.table("doctors").select("id, name").execute().data}
    except Exception as e:
        st.error(f"讀取失敗：{e}")
        return

    if not rows:
        st.info("尚無資料。請新增。")

    today = _date.today()
    combos = sorted({(r["clinic_id"], r["doctor_id"]) for r in rows})

    def _last_change(c_id, d_id):
        ds = [str(r.get("effective_from") or "")[:10] for r in rows
              if r["clinic_id"] == c_id and r["doctor_id"] == d_id]
        return max(ds) if ds else ""

    current_list, inactive_list = [], []
    for c_id, d_id in combos:
        snap = current_insurance_snapshot(rows, c_id, d_id, today)
        entry = {
            "診所": clinics.get(c_id, "?"),
            "醫師": doctors.get(d_id, "?"),
            "投保額": snap["base"],
            "勞保扣": snap["labor"],
            "健保扣": snap["nhi"],
            "最近異動": _last_change(c_id, d_id),
        }
        if snap["labor"] == 0 and snap["nhi"] == 0:
            inactive_list.append(entry)
        else:
            current_list.append(entry)

    if current_list:
        st.markdown("**📋 現行在保狀態**")
        st.dataframe(pd.DataFrame(current_list),
                     use_container_width=True, hide_index=True)
    if inactive_list:
        with st.expander(f"💤 已無投保（{len(inactive_list)} 位，含離職/退保）"):
            st.dataframe(pd.DataFrame(inactive_list),
                         use_container_width=True, hide_index=True)

    if rows:
        with st.expander("🗂️ 投保異動歷史（全部紀錄）"):
            df = pd.DataFrame(rows).copy()
            df["診所"] = df["clinic_id"].map(clinics)
            df["醫師"] = df["doctor_id"].map(doctors)
            view = df[[
                "id", "診所", "醫師", "insurance_base",
                "labor_deduction", "nhi_deduction", "effective_from", "note",
            ]].rename(columns={
                "insurance_base": "投保額",
                "labor_deduction": "勞保扣",
                "nhi_deduction": "健保扣",
                "effective_from": "異動生效日",
                "note": "備註",
            }).sort_values(["醫師", "異動生效日"])
            st.dataframe(view, use_container_width=True, hide_index=True)

    # ─── 單月試算（與薪資引擎同一套邏輯）───
    if rows:
        with st.expander("🧮 單月扣除試算（驗證按日比例）"):
            calc_month = st.date_input(
                "薪資月份（取該月 1 日）",
                value=pd.Timestamp.today().to_period("M").to_timestamp().date(),
                key="ins_calc_month",
            )
            sm = calc_month.replace(day=1).isoformat()
            trial = []
            for (c_id, d_id) in sorted({(r["clinic_id"], r["doctor_id"]) for r in rows}):
                res = insurance_for_month(rows, c_id, d_id, sm)
                if res["labor"] == 0 and res["nhi"] == 0 and res["base"] == 0:
                    continue
                trial.append({
                    "診所": clinics.get(c_id, "?"),
                    "醫師": doctors.get(d_id, "?"),
                    "投保額": res["base"],
                    "勞保扣": res["labor"],
                    "健保扣": res["nhi"],
                    "備註": res["note"] or "整月在保",
                })
            if trial:
                st.dataframe(pd.DataFrame(trial),
                             use_container_width=True, hide_index=True)
            else:
                st.info("該月份沒有任何在保配置。")

    # ─── 編輯區（需編輯模式）───
    if not st.session_state.get("edit_mode"):
        st.info("⚠️ 唯讀模式。如需新增/修改/刪除，請啟用左下「編輯模式」。")
        return

    st.divider()
    st.markdown("**📝 登記一筆投保異動**")
    st.caption(
        "選診所×醫師後，三欄自動帶入**現行值**——要改的改、不變的維持、"
        "退保的欄位改 0，填異動生效日後儲存。同一天重複儲存會覆蓋當天那筆。"
    )

    col_a, col_b = st.columns(2)
    with col_a:
        clinic_id = st.selectbox(
            "主聘診所",
            options=list(clinics.keys()),
            format_func=lambda i: clinics[i],
            key="ins_clinic",
        )
        doctor_id = st.selectbox(
            "醫師",
            options=list(doctors.keys()),
            format_func=lambda i: doctors[i],
            key="ins_doctor",
        )

    snap = current_insurance_snapshot(rows, clinic_id, doctor_id, today)
    st.info(
        f"現行狀態：投保額 {snap['base']:,}｜勞保扣 {snap['labor']:,}"
        f"｜健保扣 {snap['nhi']:,}"
        + ("（目前無投保）" if snap["labor"] == 0 and snap["nhi"] == 0 else "")
    )

    # key 帶組合 → 切換醫師時表單自動帶入該醫師現行值
    kb = f"{clinic_id}_{doctor_id}"
    with col_b:
        effective_from = st.date_input(
            "投保異動生效日（實際加/退/調整日，可為月中）",
            value=today,
            key=f"ins_from_{kb}",
        )
    col_1, col_2, col_3 = st.columns(3)
    with col_1:
        insurance_base = st.number_input(
            "投保額", min_value=0, step=100,
            value=int(snap["base"]), key=f"ins_base_{kb}",
        )
    with col_2:
        labor_deduction = st.number_input(
            "勞保扣（未保/退保填 0）", min_value=0, step=10,
            value=int(snap["labor"]), key=f"ins_labor_{kb}",
        )
    with col_3:
        nhi_deduction = st.number_input(
            "健保扣（未保/退保填 0）", min_value=0, step=10,
            value=int(snap["nhi"]), key=f"ins_nhi_{kb}",
        )

    note = st.text_input("備註（如：加保勞保、勞保退保、調投保額）", key=f"ins_note_{kb}")

    if st.button("💾 登記異動", type="primary", key="ins_save"):
        payload = {
            "clinic_id": clinic_id,
            "doctor_id": doctor_id,
            "insurance_base": insurance_base,
            "labor_deduction": labor_deduction,
            "nhi_deduction": nhi_deduction,
            "effective_from": str(effective_from),
            "effective_to": None,
            "note": note or None,
        }
        try:
            sb.table("doctor_insurance_deductions").upsert(
                payload, on_conflict="clinic_id,doctor_id,effective_from"
            ).execute()
            st.success(
                f"✅ 已登記 {doctors[doctor_id]}（{clinics[clinic_id]}）"
                f"{effective_from} 起：投保額 {insurance_base:,}｜"
                f"勞保 {labor_deduction:,}｜健保 {nhi_deduction:,}"
            )
            st.rerun()
        except Exception as e:
            st.error(f"儲存失敗：{e}")

    # ─── 進階：刪除打錯的異動列 ───
    with st.expander("🗑️ 進階：刪除單筆異動（打錯資料時用）"):
        st.caption("刪除會讓前一筆異動的狀態延續到下一筆，歷史月份重算會跟著變，請確認再刪。")
        del_opt = st.selectbox(
            "選擇異動列",
            rows,
            format_func=lambda r: (
                f"id={r['id']}｜{clinics.get(r['clinic_id'])}／{doctors.get(r['doctor_id'])}"
                f"｜{str(r.get('effective_from') or '')[:10]} 起"
                f"｜投保額 {r.get('insurance_base') or 0:,}"
                f"｜勞 {r.get('labor_deduction') or 0:,}｜健 {r.get('nhi_deduction') or 0:,}"
            ),
            key="ins_del_select",
        ) if rows else None
        if del_opt is not None and st.button("確認刪除", key="ins_del"):
            try:
                sb.table("doctor_insurance_deductions").delete().eq("id", del_opt["id"]).execute()
                st.success(f"✅ 已刪除 id={del_opt['id']}")
                st.rerun()
            except Exception as e:
                st.error(f"刪除失敗：{e}")


def _read_settings_json(sb, key: str, default):
    """讀 system_settings.value 並嘗試 JSON 解析；失敗回 default。"""
    import json
    try:
        rows = (
            sb.table("system_settings").select("value")
            .eq("key", key).limit(1).execute().data
        )
    except Exception:
        return default
    if not rows:
        return default
    v = rows[0].get("value")
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default


def _settings_pl_lists(sb):
    """月度損益分析所需的名單 / 帳號設定（system_settings）。"""
    import json
    st.subheader("月度損益分析 — 名單與帳號")
    st.caption(
        "供「月度損益分析」薪資分流、院長個人帳號排除用。\n\n"
        "- **醫師名單**：玉山薪資轉帳 / `staff_salary_summary` 內出現的姓名屬於這些 → 計入「醫師薪資」\n"
        "- **編制外人員名單**：例如澤豐謝松坊 → 計入「編制外人員薪資」\n"
        "- 其餘未列入兩名單者 → 計入「護理師&助理薪資」\n"
        "- **院長個人帳號末段**：玉山轉到這些帳號的支出視為院長個人，不計診所支出"
    )

    cur_doctors = _read_settings_json(
        sb, "doctor_names", ["周明毅", "呂敏盛", "胡舒婷"]
    )
    cur_external = _read_settings_json(sb, "external_staff_names", ["謝松坊"])
    cur_accounts = _read_settings_json(
        sb, "zhou_personal_accounts",
        ["0668979072975", "137540125004"],
    )

    if not st.session_state.get("edit_mode"):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**醫師名單**")
            st.write(cur_doctors or "—")
            st.markdown("**編制外人員名單**")
            st.write(cur_external or "—")
        with col2:
            st.markdown("**院長個人帳號（末段）**")
            st.write(cur_accounts or "—")
        st.info("⚠️ 唯讀模式。如需修改，請啟用左下「編輯模式」。")
        return

    st.divider()
    with st.form("pl_lists_form"):
        st.markdown("每行一個項目；空行會被忽略。")
        c1, c2 = st.columns(2)
        with c1:
            doctors_txt = st.text_area(
                "醫師名單", value="\n".join(cur_doctors), height=120,
            )
            external_txt = st.text_area(
                "編制外人員名單", value="\n".join(cur_external), height=120,
            )
        with c2:
            accounts_txt = st.text_area(
                "院長個人帳號（末段）", value="\n".join(cur_accounts), height=120,
                help="比對玉山 counterparty 末段。例：0668979072975、137540125004",
            )
        if st.form_submit_button("💾 儲存", type="primary"):
            def _to_list(s: str) -> list[str]:
                return [
                    line.strip() for line in s.splitlines() if line.strip()
                ]
            try:
                sb.table("system_settings").upsert(
                    [
                        {"key": "doctor_names",
                         "value": json.dumps(_to_list(doctors_txt),
                                             ensure_ascii=False),
                         "description": "月度損益分析-醫師名單"},
                        {"key": "external_staff_names",
                         "value": json.dumps(_to_list(external_txt),
                                             ensure_ascii=False),
                         "description": "月度損益分析-編制外人員名單"},
                        {"key": "zhou_personal_accounts",
                         "value": json.dumps(_to_list(accounts_txt),
                                             ensure_ascii=False),
                         "description": "院長個人帳號末段（玉山轉出排除）"},
                    ],
                    on_conflict="key",
                ).execute()
                st.success("✅ 已儲存")
                st.rerun()
            except Exception as e:
                st.error(f"儲存失敗：{e}")


def _settings_cost_params(sb):
    """成本參數（system_settings）：護理師&助理平均薪資、月上班診數，給產值估算用。"""
    st.subheader("成本參數")
    st.caption(
        "供「業績儀表板 → 醫師個人業績比較 → 產值估算 vs 成本」計算用。\n\n"
        "護理師&助理成本 = 平均薪資 / 月上班診數 × 該醫師當月診數 × **人數**\n\n"
        "人數依當月該醫師健保平均人次階梯：≤10→1；11–15→2；16–30→3；>30→4"
    )

    try:
        rows = (
            sb.table("system_settings")
            .select("key, value, description, updated_at")
            .in_("key", ["nurse_monthly_salary", "nurse_monthly_sessions"])
            .execute().data
        )
    except Exception as e:
        st.error(f"讀取失敗：{e}")
        st.info("請先在 Supabase 執行 system_settings 的 CREATE TABLE migration。")
        return

    cur = {r["key"]: r for r in rows}
    cur_salary = float((cur.get("nurse_monthly_salary") or {}).get("value", 35000))
    cur_sessions = float((cur.get("nurse_monthly_sessions") or {}).get("value", 40))
    base_per_session = (cur_salary / cur_sessions) if cur_sessions else 0

    if not st.session_state.get("edit_mode"):
        col1, col2, col3 = st.columns(3)
        col1.metric("護理師&助理平均薪資", f"NT${cur_salary:,.0f}")
        col2.metric("月上班診數", f"{cur_sessions:,.0f}")
        col3.metric(
            "基準單診成本（×人數前）",
            f"NT${base_per_session:,.0f}" if cur_sessions else "—",
        )
        st.info("⚠️ 唯讀模式。如需修改，請啟用左下「編輯模式」。")
        return

    st.divider()
    with st.form("cost_params_form"):
        c1, c2 = st.columns(2)
        with c1:
            new_salary = st.number_input(
                "護理師&助理平均薪資", min_value=0, step=1000,
                value=int(cur_salary),
            )
        with c2:
            new_sessions = st.number_input(
                "月上班診數", min_value=1, step=1,
                value=int(cur_sessions),
            )
        st.caption(
            f"預覽：基準單診成本 = {new_salary:,} / {new_sessions} = "
            f"{new_salary / new_sessions:,.0f} 元/診（最後 ×人數）"
            if new_sessions else "預覽：診數需 > 0"
        )
        if st.form_submit_button("💾 儲存", type="primary"):
            try:
                sb.table("system_settings").upsert(
                    [
                        {"key": "nurse_monthly_salary",   "value": new_salary,
                         "description": "護理師&助理平均薪資（產值估算用）"},
                        {"key": "nurse_monthly_sessions", "value": new_sessions,
                         "description": "月上班診數（產值估算用）"},
                    ],
                    on_conflict="key",
                ).execute()
                st.success("✅ 已儲存")
                st.rerun()
            except Exception as e:
                st.error(f"儲存失敗：{e}")


# ============================================================
# 6. 澤豐澤沛金流結算（Phase 4 試算插件）
# ============================================================
def page_alliance_settlement():
    """
    澤豐澤沛金流結算頁（試算插件，不影響月度 P&L）

    結算 = 商品調貨試算 + 員工跨代付薪資 + 醫師跨支援薪資
    淨額 > 0 → 沛 應付 豐；淨額 < 0 → 豐 應付 沛

    P.S. 實際 x6（豐沛金流）認列仍以中信收付為主，本頁僅供院方安排
    每月實際匯款金額參考。
    """
    from data_processor.inventory_pricing import (
        compute_inventory_amounts, summarize,
    )
    from data_processor.salary import run_salary_calculation

    st.title("🤝 澤豐澤沛金流結算")
    st.info(
        "ℹ️ **試算插件，不影響月度 P&L**。本頁將商品調貨、員工跨代付、"
        "醫師跨支援的金流彙總成淨額，作為每月實際匯款的依據。"
        "實際 x6（豐沛金流）認列仍以中信收付為基準。"
    )

    sb = get_authed_client()

    # ─── 診所 id ───
    clinics = {
        c["short_name"]: c["id"]
        for c in sb.table("clinics").select("id, short_name").execute().data
    }
    fz_id = clinics.get("澤豐")
    fp_id = clinics.get("澤沛")
    if not (fz_id and fp_id):
        st.error("找不到澤豐/澤沛診所")
        return

    # ─── 月份候選（彙總三個來源）───
    months_set: set[str] = set()
    try:
        for r in (sb.table("inventory_transfer")
                    .select("transfer_month").execute().data or []):
            months_set.add(r["transfer_month"])
    except Exception:
        pass
    try:
        for r in (sb.table("staff_salary_summary")
                    .select("service_month").execute().data or []):
            months_set.add(r["service_month"])
    except Exception:
        pass
    try:
        for r in (sb.table("doctor_visit_stats")
                    .select("service_month").execute().data or []):
            months_set.add(r["service_month"])
    except Exception:
        pass
    months_sorted = sorted(_filter_min_month(months_set), reverse=True)
    if not months_sorted:
        st.warning("⚠️ 尚無資料可結算")
        return

    col1, _ = st.columns([2, 5])
    with col1:
        service_month = st.selectbox(
            "結算月份", months_sorted,
            format_func=lambda d: d[:7], key="settlement_month",
        )

    # ─────────────────────────────────────────────
    # 0. DB 健康診斷
    # ─────────────────────────────────────────────
    def _safe_count(table_name: str, **filters) -> int | str:
        try:
            q = sb.table(table_name).select("*", count="exact")
            for k, v in filters.items():
                q = q.eq(k, v)
            resp = q.limit(1).execute()
            return resp.count if resp.count is not None else len(resp.data or [])
        except Exception as e:
            return f"❌ {e!s:.40}"

    inv_count = _safe_count("inventory_transfer", transfer_month=service_month)
    pp_count = _safe_count("product_pricing")
    tcm_count = _safe_count("tcm_concentrate_pricing")
    ss_count = _safe_count("staff_salary_summary", service_month=service_month)

    diag_rows = [
        {
            "資料來源": "inventory_transfer",
            "範圍": f"{service_month[:7]}",
            "筆數": inv_count,
            "說明": "本月調貨明細（沛↔豐）",
        },
        {
            "資料來源": "product_pricing",
            "範圍": "全表",
            "筆數": pp_count,
            "說明": "自費商品成本（含廠商品項以外的 lookup）",
        },
        {
            "資料來源": "tcm_concentrate_pricing",
            "範圍": "全表",
            "筆數": tcm_count,
            "說明": "科中進貨價目（廠商品項 lookup）",
        },
        {
            "資料來源": "staff_salary_summary",
            "範圍": f"{service_month[:7]}",
            "筆數": ss_count,
            "說明": "員工薪資（含跨代付）",
        },
    ]
    with st.expander("🩺 DB 健康診斷（先看這個）", expanded=False):
        st.dataframe(
            pd.DataFrame(diag_rows), use_container_width=True, hide_index=True
        )
        warnings = []
        if isinstance(tcm_count, int) and tcm_count == 0:
            warnings.append(
                "⚠️ **科中進貨價目表為空**：所有含廠商的科中品項都會列為未匹配。"
                "請至「📥 本月資料匯入區 → 科中進貨價目表」上傳。"
            )
        if isinstance(pp_count, int) and pp_count == 0:
            warnings.append(
                "⚠️ **自費商品成本表為空**：所有非廠商品項（護腰/膠囊/水藥等）"
                "都會列為未匹配。請至「📥 本月資料匯入區 → 自費商品成本&售價」上傳。"
            )
        if isinstance(inv_count, int) and inv_count == 0:
            warnings.append(
                f"⚠️ **{service_month[:7]} 無調貨資料**：請至「調貨整理」上傳年度檔。"
            )
        for w in warnings:
            st.warning(w)
        if not warnings:
            st.success("✅ 全部資料表皆有資料")

    # ─────────────────────────────────────────────
    # 1. 商品調貨試算
    # ─────────────────────────────────────────────
    st.subheader("🛒 商品調貨試算")

    inv_rows = (
        sb.table("inventory_transfer")
        .select("transfer_month, from_clinic_id, to_clinic_id, item, qty")
        .eq("transfer_month", service_month)
        .execute().data
    ) or []

    if not inv_rows:
        st.caption(f"⚠️ {service_month[:7]} 無調貨資料")
        product_pei_to_feng = 0.0
        product_feng_to_pei = 0.0
        priced_items: list = []
        unmatched: list = []
    else:
        # Supabase PostgREST 預設單次 SELECT 上限 1000 筆；
        # tcm_concentrate_pricing 有 1300+ 筆 → 用 range 分頁全部取回。
        def _fetch_all(table_name: str, cols: str, chunk: int = 1000):
            out: list[dict] = []
            offset = 0
            while True:
                resp = (
                    sb.table(table_name).select(cols)
                    .range(offset, offset + chunk - 1).execute()
                )
                rows = resp.data or []
                out.extend(rows)
                if len(rows) < chunk:
                    break
                offset += chunk
            return out

        try:
            tcm_rows = _fetch_all(
                "tcm_concentrate_pricing", "category, vendor, product_name, price"
            )
        except Exception as e:
            st.warning(f"科中價目表讀取失敗（{e}）— 含廠商品項都會被列為未匹配")
            tcm_rows = []
        try:
            pp_rows = _fetch_all(
                "product_pricing", "vendor, product_name, cost_price"
            )
        except Exception as e:
            st.warning(f"自費商品價目表讀取失敗（{e}）")
            pp_rows = []

        priced_items = compute_inventory_amounts(
            inv_rows, tcm_rows, pp_rows, fz_id, fp_id
        )
        summary_inv = summarize(priced_items)
        unmatched = summary_inv["unmatched"]

        product_pei_to_feng = summary_inv["by_month_dir"].get(
            (service_month, "沛PAY豐"), {"total": 0.0}
        )["total"]
        product_feng_to_pei = summary_inv["by_month_dir"].get(
            (service_month, "豐PAY沛"), {"total": 0.0}
        )["total"]

        cols = st.columns(2)
        with cols[0]:
            st.metric("沛PAY豐 商品小計", f"${product_pei_to_feng:,.0f}")
        with cols[1]:
            st.metric("豐PAY沛 商品小計", f"${product_feng_to_pei:,.0f}")

        with st.expander(f"📦 沛PAY豐 明細（澤豐 → 澤沛 商品調貨）"):
            rows = [
                {
                    "品項": it.item,
                    "qty": it.qty,
                    "廠商": it.vendor or "—",
                    "單價": it.unit_price,
                    "比例": it.ratio,
                    "金額": it.amount,
                    "來源": it.source,
                    "備註": it.note or "",
                }
                for it in priced_items if it.direction == "沛PAY豐"
            ]
            if rows:
                st.dataframe(
                    pd.DataFrame(rows), use_container_width=True, hide_index=True
                )
            else:
                st.caption("無資料")

        with st.expander(f"📦 豐PAY沛 明細（澤沛 → 澤豐 商品調貨）"):
            rows = [
                {
                    "品項": it.item,
                    "qty": it.qty,
                    "廠商": it.vendor or "—",
                    "單價": it.unit_price,
                    "比例": it.ratio,
                    "金額": it.amount,
                    "來源": it.source,
                    "備註": it.note or "",
                }
                for it in priced_items if it.direction == "豐PAY沛"
            ]
            if rows:
                st.dataframe(
                    pd.DataFrame(rows), use_container_width=True, hide_index=True
                )
            else:
                st.caption("無資料")

        if unmatched:
            with st.expander(
                f"⚠️ 未匹配品項（{len(unmatched)} 筆，未計入金額）"
            ):
                st.caption(
                    "原因：該品項在「自費商品成本&售價」或「科中進貨價目表」中找不到對應"
                    "（已含別名與前 3/5 字模糊比對；模糊比對有 2 個以上候選時"
                    "不自動帶入，候選列於「原因」欄供人工確認）。"
                    "請至「本月資料匯入區 → 自費商品/科中進貨價目」上傳最新表，或"
                    "確認品項命名一致。"
                )
                rows = [
                    {
                        "方向": it.direction,
                        "品項": it.item,
                        "qty": it.qty,
                        "原因": it.note,
                    } for it in unmatched
                ]
                st.dataframe(
                    pd.DataFrame(rows), use_container_width=True, hide_index=True
                )

    # ─────────────────────────────────────────────
    # 2. 員工跨代付薪資
    # ─────────────────────────────────────────────
    st.divider()
    st.subheader("👥 員工跨代付薪資")
    st.caption(
        "來源：staff_salary_summary（paid_by_clinic_id IS NOT NULL）。"
        "員工屬澤豐、澤沛代付 → 豐PAY沛；員工屬澤沛、澤豐代付 → 沛PAY豐。"
    )

    try:
        ss_rows = (
            sb.table("staff_salary_summary")
            .select("clinic_id, paid_by_clinic_id, employee_label, gross_salary")
            .eq("service_month", service_month)
            .not_.is_("paid_by_clinic_id", "null")
            .execute().data
        ) or []
    except Exception as e:
        st.warning(f"員工薪資讀取失敗：{e}")
        ss_rows = []

    staff_pei_to_feng = 0.0  # 員工屬澤沛(clinic=fp), 澤豐墊付(paid_by=fz) → 沛PAY豐
    staff_feng_to_pei = 0.0  # 員工屬澤豐(clinic=fz), 澤沛墊付(paid_by=fp) → 豐PAY沛
    staff_detail = []
    for r in ss_rows:
        cid = r["clinic_id"]
        pid = r["paid_by_clinic_id"]
        amt = float(r.get("gross_salary") or 0)
        if cid == fp_id and pid == fz_id:
            staff_pei_to_feng += amt
            direction = "沛PAY豐"
        elif cid == fz_id and pid == fp_id:
            staff_feng_to_pei += amt
            direction = "豐PAY沛"
        else:
            continue
        staff_detail.append({
            "方向": direction,
            "員工": r["employee_label"],
            "歸屬診所": "澤豐" if cid == fz_id else "澤沛",
            "代付方": "澤豐" if pid == fz_id else "澤沛",
            "金額": amt,
        })

    cols = st.columns(2)
    with cols[0]:
        st.metric("沛PAY豐 員工薪資", f"${staff_pei_to_feng:,.0f}")
    with cols[1]:
        st.metric("豐PAY沛 員工薪資", f"${staff_feng_to_pei:,.0f}")

    if staff_detail:
        with st.expander("👤 員工跨代付明細"):
            st.dataframe(
                pd.DataFrame(staff_detail),
                use_container_width=True, hide_index=True,
            )
    else:
        st.caption("⚠️ 無跨代付員工資料")

    # ─────────────────────────────────────────────
    # 3. 醫師跨支援薪資
    # ─────────────────────────────────────────────
    st.divider()
    st.subheader("🩺 醫師跨支援薪資")
    st.caption(
        "來源：醫師薪資計算結果。主聘澤豐到澤沛支援 → 沛PAY豐；"
        "主聘澤沛到澤豐支援 → 豐PAY沛。（與「醫師薪資計算」頁的"
        "「跨支援墊付」表完全一致）"
    )

    doctor_pei_to_feng = 0.0
    doctor_feng_to_pei = 0.0
    doctor_detail = []
    try:
        _, payslips = run_salary_calculation(sb, service_month)
        for p in payslips:
            if p.gross_support <= 0 or not p.support_clinic_id:
                continue
            amt = float(p.gross_support)
            # 主聘=澤豐 (墊付方), 支援去=澤沛 → 看診診所=澤沛 應還 → 沛PAY豐
            if p.main_clinic_id == fz_id and p.support_clinic_id == fp_id:
                doctor_pei_to_feng += amt
                direction = "沛PAY豐"
            elif p.main_clinic_id == fp_id and p.support_clinic_id == fz_id:
                doctor_feng_to_pei += amt
                direction = "豐PAY沛"
            else:
                continue
            doctor_detail.append({
                "方向": direction,
                "醫師": p.doctor_name,
                "墊付方(主聘)": p.main_clinic_name,
                "看診診所(應還)": p.support_clinic_name,
                "金額": amt,
            })
    except Exception as e:
        st.warning(f"醫師薪資計算失敗：{e}")

    cols = st.columns(2)
    with cols[0]:
        st.metric("沛PAY豐 醫師薪資", f"${doctor_pei_to_feng:,.0f}")
    with cols[1]:
        st.metric("豐PAY沛 醫師薪資", f"${doctor_feng_to_pei:,.0f}")

    if doctor_detail:
        with st.expander("🩺 醫師跨支援明細"):
            st.dataframe(
                pd.DataFrame(doctor_detail),
                use_container_width=True, hide_index=True,
            )
    else:
        st.caption("⚠️ 無跨支援醫師資料")

    # ─────────────────────────────────────────────
    # 4. 結算總覽
    # ─────────────────────────────────────────────
    st.divider()
    st.subheader("📊 結算總覽")

    pei_to_feng_total = (
        product_pei_to_feng + staff_pei_to_feng + doctor_pei_to_feng
    )
    feng_to_pei_total = (
        product_feng_to_pei + staff_feng_to_pei + doctor_feng_to_pei
    )
    net = pei_to_feng_total - feng_to_pei_total

    summary_df = pd.DataFrame([
        {
            "類別": "商品調貨",
            "沛PAY豐": product_pei_to_feng,
            "豐PAY沛": product_feng_to_pei,
        },
        {
            "類別": "員工跨代付",
            "沛PAY豐": staff_pei_to_feng,
            "豐PAY沛": staff_feng_to_pei,
        },
        {
            "類別": "醫師跨支援",
            "沛PAY豐": doctor_pei_to_feng,
            "豐PAY沛": doctor_feng_to_pei,
        },
        {
            "類別": "🟦 合計",
            "沛PAY豐": pei_to_feng_total,
            "豐PAY沛": feng_to_pei_total,
        },
    ])
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    cols = st.columns(3)
    with cols[0]:
        st.metric("沛PAY豐 合計", f"${pei_to_feng_total:,.0f}")
    with cols[1]:
        st.metric("豐PAY沛 合計", f"${feng_to_pei_total:,.0f}")
    with cols[2]:
        if net > 0:
            st.metric("🟢 淨額", f"${net:,.0f}", delta="澤沛 應付 澤豐")
        elif net < 0:
            st.metric("🟢 淨額", f"${abs(net):,.0f}", delta="澤豐 應付 澤沛")
        else:
            st.metric("🟢 淨額", "$0", delta="兩邊打平")

    st.caption(
        "💡 結算後實際匯款一筆於下個月初，匯款紀錄會出現在中信戶 → "
        "由月度 P&L 自動認列為 x6（豐沛金流）。"
    )
