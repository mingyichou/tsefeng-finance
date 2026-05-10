"""
功能頁面集合
每個 page_xxx() 對應 sidebar 一個選單項
"""

import streamlit as st
import pandas as pd
from db import get_authed_client


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

    all_months = sorted(set(
        list(out_df["service_month"].unique() if not out_df.empty else [])
        + list(cash_df["service_month"].unique() if not cash_df.empty else [])
        + list(visit_df["service_month"].unique() if not visit_df.empty else [])
    ), reverse=True)
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

            # 每個診所一張小圖；左半=澤豐、右半=澤沛
            sub_charts = []
            for c_short in ["澤豐", "澤沛"]:
                sub = rates_f[rates_f["診所"] == c_short].sort_values("月份")
                if sub.empty:
                    continue
                bar_count = alt.Chart(sub).mark_bar(
                    xOffset=-14, width=24, color="#6A5ACD",
                ).encode(
                    x=alt.X("月份:N", sort="ascending", title=None),
                    y=alt.Y("初診人數:Q",
                            axis=alt.Axis(title="初診人數", titleColor="#6A5ACD")),
                    tooltip=["月份", alt.Tooltip("初診人數:Q", format=",")],
                )
                bar_rate = alt.Chart(sub).mark_bar(
                    xOffset=14, width=24, color="#FFA07A",
                ).encode(
                    x=alt.X("月份:N", sort="ascending", title=None),
                    y=alt.Y("初診率(%):Q",
                            axis=alt.Axis(title="初診率(%)", titleColor="#FFA07A",
                                          orient="right")),
                    tooltip=["月份", alt.Tooltip("初診率(%):Q", format=".2f")],
                )
                sub_chart = alt.layer(bar_count, bar_rate).resolve_scale(
                    y="independent"
                ).properties(title=c_short, height=320)
                sub_charts.append(sub_chart)

            if sub_charts:
                combined = alt.hconcat(*sub_charts).resolve_scale(color="independent")
                st.altair_chart(combined, use_container_width=True)

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
    """醫師產值估算公式拆解（澤豐/澤沛兩套）。回傳所有中間值。

    澤豐：(診察費 + 內科費*0.2 + 處(內+xx)*0.3 + 純xx*0.5 + 調劑費)*0.9
         + 掛號費 + 自費(內服+外用+保養+飲片)*0.3 + 自費(針+傷+脫)*0.4
         + 自費(檢驗)*0.8 + 自費(診察) + 自費(其他)
    澤沛：(診察費 + 藥費*0.2 + 處置費*0.5 + 調劑費)*0.9 + 同上自費部分
    """
    g = lambda d, k: (d.get(k) or 0) if d else 0
    consult_nhi = g(out_row, "nhi_consult_fee")
    drug_nhi = g(out_row, "nhi_drug_fee")
    dispense_nhi = g(out_row, "nhi_dispense_fee")
    if clinic_short == "澤豐":
        combo = g(out_row, "nhi_combo_treatment")
        pure = g(out_row, "nhi_pure_treatment")
        treatment_single = 0
        nhi_pre = (consult_nhi + drug_nhi * 0.2
                   + combo * 0.3 + pure * 0.5 + dispense_nhi)
    else:  # 澤沛
        combo = pure = 0
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
        "處(內+xx)" if clinic_short == "澤豐" else "處置費":
            combo if clinic_short == "澤豐" else treatment_single,
        "純xx": pure,  # 澤沛恆 0
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
    months = sorted(set(m_out + m_vis), reverse=True)
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
        "支援醫師欄位 = 各正職「補支援醫師數」歸給該診所支援醫師（從末階段往前扣）。"
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

        def _add_cap(scope: str, name: str, stages: list[int]):
            if sum(stages) == 0:
                return
            for i, stg in enumerate(stages):
                rows4.append({
                    "分組": scope, "醫師": name,
                    "顯示": f"{SCOPE_PREFIX[scope]} {name}",
                    "排序": {"澤豐": 1, "澤沛": 2, "全院": 3}[scope],
                    "階段": STAGE_NAMES[i],
                    "階段序": i,
                    "人次": int(stg),
                })

        def _stages_of(c_id: int, d_id: int) -> list[int]:
            r = cap_idx.get((c_id, d_id))
            if not r:
                return [0] * 5
            return [r.get(f"stage{i+1}") or 0 for i in range(5)]

        for name in fz_doctors:
            _add_cap("澤豐", name, _stages_of(fz_id, name_to_did[name]))
        for name in fp_doctors:
            _add_cap("澤沛", name, _stages_of(fp_id, name_to_did[name]))
        for name in all_doctors:
            d_id = name_to_did[name]
            both = [a + b for a, b in zip(
                _stages_of(fz_id, d_id), _stages_of(fp_id, d_id))]
            _add_cap("全院", name, both)

        if rows4:
            df4 = pd.DataFrame(rows4)
            order4 = (df4[["顯示", "排序", "醫師"]].drop_duplicates()
                      .sort_values(["排序", "醫師"])["顯示"].tolist())
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
# 2. 收支總覽（Phase 4 月度損益）
# ============================================================


def _render_data_health(sb, service_month: str):
    """
    顯示該月份所有資料來源的筆數，0 筆會 ⚠️ 警告。
    用途：跨月切換時，院長可一眼看出某月某帳戶/某資料源是否缺資料，
          避免誤判為系統 bug。
    """
    from collections import Counter
    from data_processor.monthly_pl import _next_month, _prev_month

    next_m = _next_month(service_month)
    prev_m = _prev_month(service_month)
    sm_label = service_month[:7]
    pm_label = prev_m[:7]

    # 4 個關鍵銀行帳戶
    accounts = (
        sb.table("bank_accounts")
        .select("id, clinic_id, bank, account_type, is_personal_mixed")
        .execute().data
    )
    clinic_resp = sb.table("clinics").select("id, short_name").execute().data
    cid_to_short = {c["id"]: c["short_name"] for c in clinic_resp}
    fz_id = next((c["id"] for c in clinic_resp if c["short_name"] == "澤豐"), None)
    fp_id = next((c["id"] for c in clinic_resp if c["short_name"] == "澤沛"), None)

    # 一次撈本月所有 bank_transactions count by account_id
    tx_rows = (
        sb.table("bank_transactions").select("account_id")
        .gte("transaction_date", service_month).lt("transaction_date", next_m)
        .execute().data
    )
    tx_counts = Counter(r["account_id"] for r in tx_rows)

    bank_table = []
    issues: list[str] = []
    for acc in accounts:
        clinic = cid_to_short.get(acc["clinic_id"], "?")
        bank = acc.get("bank", "?")
        atype = acc.get("account_type", "?")
        if acc.get("is_personal_mixed"):
            atype = f"{atype}（混戶）"
        n = tx_counts.get(acc["id"], 0)
        bank_table.append({
            "診所": clinic,
            "戶別": f"{bank} {atype}",
            f"{sm_label} 筆數": n,
            "狀態": "✅" if n > 0 else "⚠️ 缺",
        })
        if n == 0:
            issues.append(f"{clinic} {bank} {atype}：{sm_label} CSV 未上傳")

    # 其他關鍵資料源
    def _count(table: str, filters: list[tuple[str, str, object]]) -> int:
        q = sb.table(table).select("id", count="exact")
        for op, col, val in filters:
            q = getattr(q, op)(col, val)
        return q.execute().count or 0

    other_rows = []
    if fz_id:
        n = _count("cash_expense", [("eq", "clinic_id", fz_id), ("eq", "accrual_month", service_month)])
        other_rows.append({"資料源": "x3 澤豐現金支出 (cash_expense)", "月份": sm_label, "筆數": n,
                           "狀態": "✅" if n > 0 else "⚠️ 缺"})
        if n == 0:
            issues.append(f"x3 cash_expense {sm_label} 缺資料：請上傳澤豐現金支出 xlsx")

        n = _count("contract_expense", [("eq", "clinic_id", fz_id), ("eq", "service_month", service_month)])
        other_rows.append({"資料源": "x12 澤豐合約支出 (contract_expense)", "月份": sm_label, "筆數": n,
                           "狀態": "✅" if n > 0 else "⚠️ 缺"})
        if n == 0:
            issues.append(f"x12 contract_expense {sm_label} 缺資料：請上傳澤豐合約支出 xlsx")

        # x9 謝松坊：staff_salary_summary 在 prev_m 是否有「謝松坊」
        rows_x9 = (
            sb.table("staff_salary_summary")
            .select("id, employee_label")
            .eq("clinic_id", fz_id).eq("service_month", prev_m)
            .execute().data
        )
        n_x9 = sum(1 for r in rows_x9 if "謝松坊" in (r.get("employee_label") or ""))
        other_rows.append({"資料源": f"x9 謝松坊薪資 ({pm_label} 服務月)", "月份": pm_label, "筆數": n_x9,
                           "狀態": "✅" if n_x9 > 0 else "⚠️ 缺"})
        if n_x9 == 0:
            issues.append(f"x9 staff_salary_summary {pm_label} 沒謝松坊：請到員工薪資頁按「全部月份一次匯入」")

        # x13 周院長：doctor_salary_monthly prev_m 是否有周明毅
        zhou = sb.table("doctors").select("id").eq("name", "周明毅").execute().data
        if zhou:
            zhou_id = zhou[0]["id"]
            n_x13 = _count("doctor_salary_monthly", [
                ("eq", "doctor_id", zhou_id), ("eq", "service_month", prev_m),
            ])
            other_rows.append({"資料源": f"x13 周院長薪資 ({pm_label} 服務月)", "月份": pm_label, "筆數": n_x13,
                               "狀態": "✅" if n_x13 > 0 else "⚠️ 缺"})
            if n_x13 == 0:
                issues.append(f"x13 doctor_salary_monthly {pm_label} 沒周院長：請到醫師薪資頁選 {pm_label} 並按「💾 寫入」")

        # x8 manual_annotation：澤豐&個人中信「存現」標記
        ann_rows = (
            sb.table("manual_annotation").select("id, description")
            .eq("scope", "診所").eq("form", "存現").eq("account", "澤豐&個人中信")
            .eq("clinic_id", fz_id)
            .gte("entry_date", service_month).lt("entry_date", next_m)
            .execute().data
        )
        other_rows.append({"資料源": "x8 manual_annotation（澤豐存現標記）", "月份": sm_label,
                           "筆數": len(ann_rows),
                           "狀態": "✅" if len(ann_rows) > 0 else "ℹ️ 無"})
        # x8 缺註記不一定是問題（該月沒存現），不放進 issues

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
    st.title("💰 月度實帳收支總覽")

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

    with st.spinner("計算中..."):
        pl_fz, pl_fp = calculate_both_clinics(sb, service_month)
        check = calculate_check_expense_month(sb, service_month)

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
    ])
    st.dataframe(fz_ex_summary, use_container_width=True, hide_index=True)

    if pl_fz.esun_outflow_items:
        with st.expander(f"📑 玉山出帳明細（{len(pl_fz.esun_outflow_items)} 筆）"):
            _show_items(pl_fz.esun_outflow_items, _BANK_COLS)
    if pl_fz.x3_items:
        with st.expander(f"📑 x3 澤豐現金支出明細（{len(pl_fz.x3_items)} 筆）"):
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
    ])
    st.dataframe(fp_ex_summary, use_container_width=True, hide_index=True)
    st.caption("ℹ️ x5 / x6 / x7 已包含於「中信逐筆出帳」中，分類項僅供識別不重複加總。")

    if pl_fp.esun_outflow_items:
        with st.expander(f"📑 玉山出帳明細（{len(pl_fp.esun_outflow_items)} 筆）"):
            _show_items(pl_fp.esun_outflow_items, _BANK_COLS)
    if pl_fp.ctbc_outflow_items:
        with st.expander(f"📑 中信出帳明細（{len(pl_fp.ctbc_outflow_items)} 筆，含 settle 標註）"):
            _show_items(
                pl_fp.ctbc_outflow_items,
                ["transaction_date", "summary", "counterparty", "amount", "note", "settle_kind", "attribution_month"],
            )
    if pl_fp.x10_expense_items:
        with st.expander(f"📑 x10 手 KEY 支出明細（{len(pl_fp.x10_expense_items)} 筆）"):
            _show_items(pl_fp.x10_expense_items)

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
            tfz, tfp = calculate_both_clinics(sb, m)
            tchk = calculate_check_expense_month(sb, m)
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
        "檔名範例：『澤豐中醫診所現金支出.xlsx』。"
        "檔內描述以「支票-XXX(銀行)」開頭的列會自動分流到 check_expense 表，"
        "其他歸 cash_expense。**不需另外上傳支票檔。**"
    )

    st.info(
        "ℹ️ **澤沛現金支出由系統從澤沛中信交易自動辨識**（標籤如「沛02月現金支出」），"
        "不需上傳檔案。本區僅供澤豐使用。"
    )

    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        clinic_choice = st.radio("診所", ["澤豐"], key="cash_exp_clinic")
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


def _section_manual_annotation():
    """金流補充備註 — 補齊銀行帳戶/帳本未記載的說明（CRUD）"""
    st.subheader("📝 手 KEY：金流補充備註")
    st.caption(
        "用於補齊銀行帳戶/帳本中未記載的備註說明。"
        "例：某筆轉帳實際是「個人借款還款」、某筆存現是「投資收益」。"
        "可隨時查詢/修改/刪除。"
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
        cols = ["id", "entry_date", "scope", "form", "account",
                "amount", "診所", "description"]
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

    description = st.text_area(
        "備註說明",
        value=sel.get("description") or "" if sel else "",
        placeholder="例：個人借款還款、廠商紅利、退費...",
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
        "金額暫不算（等 Sprint 2.8 自費商品成本售價表上線後由 trigger 帶入）。"
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
            payload = [
                {k: v for k, v in r.items() if k != "方向"}
                for r in records
            ]
            # UNIQUE (transfer_month, from_clinic_id, to_clinic_id, item)
            # 重複上傳會覆蓋同組鍵的 qty / unit_price / amount
            sb.table("inventory_transfer").upsert(
                payload,
                on_conflict="transfer_month,from_clinic_id,to_clinic_id,item",
            ).execute()
            st.success(f"✅ 寫入 {len(payload)} 筆（同月+同方向+同品項會覆蓋舊值）")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _section_outpatient_report():
    """門診申報金額統計報表 + A91+複針補表（Sprint 2.4）"""
    from data_processor.clinic_report import (
        detect_format,
        parse_fz_main, parse_fp_main, parse_fp_a91,
    )

    st.subheader("📊 門診申報金額統計報表 + A91+複針（批次）")
    st.caption(
        "三種版式自動識別：澤豐 48 欄主表 / 澤沛 16 欄主表 / 澤沛 A91+複針 137 欄補表。"
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
        "fz_main": parse_fz_main,
        "fp_main": parse_fp_main,
        "fp_a91": parse_fp_a91,
    }
    kind_label = {
        "fz_main": "澤豐 48 欄",
        "fp_main": "澤沛 16 欄",
        "fp_a91": "澤沛 A91+複針 137 欄",
    }

    for f in uploaded_files:
        try:
            meta = detect_format(f.name)
            cid = short_to_cid[meta["clinic_short"]]
            recs = parser_map[meta["kind"]](f, f.name, cid, name_to_did)
            if meta["kind"] == "fp_a91":
                a91_records.extend(recs)
            else:
                main_records.extend(recs)
            summaries.append({
                "檔名": f.name,
                "版式": kind_label[meta["kind"]],
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

    # 該診所支援醫師（role='support'）
    dc_resp = (sb.table("doctor_clinic")
               .select("clinic_id, doctor_id, role")
               .eq("role", "support").execute())
    support_by_clinic: dict[int, list[int]] = {}
    for r in dc_resp.data:
        support_by_clinic.setdefault(r["clinic_id"], []).append(r["doctor_id"])

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
            sup_list = support_by_clinic.get(cid, [])
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
    months_set = sorted({r["service_month"] for r in months_resp.data}, reverse=True)
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
        })
    st.dataframe(
        pd.DataFrame(pay_rows), use_container_width=True, hide_index=True,
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
    comps, ps, cash_lookup: dict, role_label: dict, service_month: str
) -> str:
    """產生單一醫師薪資單的完整 HTML（給下載+瀏覽器列印用）"""
    doctor_name = comps[0].doctor_name
    title = f"{doctor_name} 薪資單 {service_month[:7]}"

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
    @media print {
        @page { margin: 1.5cm; }
        body { margin: 0; }
        .total { background: #fff; border: 1px solid #6A5ACD; }
    }
    </style>
    """

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
def page_personal():
    st.title("💸 院長個人財富分析")

    if not st.session_state.get("edit_mode", False):
        st.warning("⚠️ 唯讀模式。可檢視歷史透支報表，但無法編輯本月公式變數。")
    else:
        st.success("✅ 編輯模式啟用中")

    st.info("🚧 開發中（Phase 5：11 變數 + 動用診所盈餘金額計算）")

    st.markdown("**本頁將顯示：**")
    st.markdown("""
    - 月度 11 個透支變數（x1~x11）的明細
    - n1（中信戶個人支出）、n2（玉山戶個人支出）
    - **動用診所盈餘金額** = N - C - S
    - 12 個月趨勢折線圖
    - 公式檢視：每個變數可點開看原始資料來源
    """)


# ============================================================
# 5. 系統設定
# ============================================================
def page_settings():
    st.title("⚙️ 系統設定")

    sb = get_authed_client()

    tab1, tab2, tab_ins, tab_cost, tab3 = st.tabs(
        ["白名單使用者", "醫師主檔", "勞健保扣除額", "成本參數", "系統資訊"]
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
        st.subheader("醫師-診所角色配置")
        try:
            sql = """
            SELECT d.name AS 醫師, c.short_name AS 診所, dc.role AS 角色,
                   dc.director_allowance AS 院長津貼, d.session_fee AS 診薪
            FROM doctor_clinic dc
            JOIN doctors d ON d.id = dc.doctor_id
            JOIN clinics c ON c.id = dc.clinic_id
            ORDER BY c.short_name, dc.role
            """
            # Supabase Python SDK 不直接支援 raw SQL，改用 RPC 或表 join
            # 先用簡單方式：分開查再 merge
            doctors = pd.DataFrame(sb.table("doctors").select("*").execute().data)
            clinics = pd.DataFrame(sb.table("clinics").select("*").execute().data)
            dc = pd.DataFrame(sb.table("doctor_clinic").select("*").execute().data)
            if not dc.empty:
                merged = dc.merge(
                    doctors.rename(columns={"id": "doctor_id"}), on="doctor_id"
                ).merge(
                    clinics.rename(columns={"id": "clinic_id"}), on="clinic_id"
                )[["name", "short_name", "role", "director_allowance", "session_fee"]]
                merged.columns = ["醫師", "診所", "角色", "院長津貼", "診薪"]
                st.dataframe(merged, use_container_width=True)
        except Exception as e:
            st.error(f"讀取失敗：{e}")

    with tab_ins:
        _settings_insurance_deductions(sb)

    with tab_cost:
        _settings_cost_params(sb)

    with tab3:
        st.subheader("系統資訊")
        st.text(f"登入者：{st.session_state.session.get('email')}")
        st.text(f"角色：{st.session_state.get('user_role', {}).get('role', 'unknown')}")
        st.text(f"User ID：{st.session_state.session.get('user_id')}")
        st.caption("Supabase URL：" + st.secrets["supabase"]["url"])


def _settings_insurance_deductions(sb):
    """勞健保扣除額管理（在主聘診所×醫師配置；UI CRUD）"""
    st.subheader("勞健保扣除額")
    st.caption(
        "規則：只在主聘診所扣一次，支援診所扣 0。"
        "目前所有醫師勞保扣 = 0（未加入勞保）。"
        "投保額異動或新增醫師時在此編輯，下次計算薪資自動套用。"
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

    df = pd.DataFrame(rows).copy() if rows else pd.DataFrame()
    if not df.empty:
        df["診所"] = df["clinic_id"].map(clinics)
        df["醫師"] = df["doctor_id"].map(doctors)
        view = df[[
            "id", "診所", "醫師", "insurance_base",
            "labor_deduction", "nhi_deduction",
            "effective_from", "effective_to", "note",
        ]].rename(columns={
            "insurance_base": "投保額",
            "labor_deduction": "勞保扣",
            "nhi_deduction": "健保扣",
            "effective_from": "生效起",
            "effective_to": "結束",
            "note": "備註",
        })
        st.dataframe(view, use_container_width=True, hide_index=True)

    # ─── 編輯區（需編輯模式）───
    if not st.session_state.get("edit_mode"):
        st.info("⚠️ 唯讀模式。如需新增/修改/刪除，請啟用左下「編輯模式」。")
        return

    st.divider()
    st.markdown("**新增 / 修改一筆配置**")

    edit_id = st.selectbox(
        "選擇要修改的列（或留「新增」建立新列）",
        options=["（新增）"] + [f"id={r['id']} {clinics.get(r['clinic_id'])}/{doctors.get(r['doctor_id'])}" for r in rows],
        key="ins_edit_select",
    )
    is_edit = edit_id != "（新增）"
    selected = None
    if is_edit:
        sid = int(edit_id.split()[0].split("=")[1])
        selected = next((r for r in rows if r["id"] == sid), None)

    col_a, col_b = st.columns(2)
    with col_a:
        clinic_id = st.selectbox(
            "主聘診所",
            options=list(clinics.keys()),
            format_func=lambda i: clinics[i],
            index=list(clinics.keys()).index(selected["clinic_id"]) if selected else 0,
            key="ins_clinic",
        )
        doctor_id = st.selectbox(
            "醫師",
            options=list(doctors.keys()),
            format_func=lambda i: doctors[i],
            index=list(doctors.keys()).index(selected["doctor_id"]) if selected else 0,
            key="ins_doctor",
        )
        insurance_base = st.number_input(
            "投保額",
            min_value=0, step=100,
            value=int(selected["insurance_base"]) if selected else 0,
            key="ins_base",
        )
    with col_b:
        labor_deduction = st.number_input(
            "勞保扣（目前皆 0）",
            min_value=0, step=10,
            value=int(selected["labor_deduction"] or 0) if selected else 0,
            key="ins_labor",
        )
        nhi_deduction = st.number_input(
            "健保扣",
            min_value=0, step=10,
            value=int(selected["nhi_deduction"] or 0) if selected else 0,
            key="ins_nhi",
        )
        effective_from = st.date_input(
            "生效起始月（含）",
            value=(
                pd.to_datetime(selected["effective_from"]).date()
                if selected and selected.get("effective_from") else pd.Timestamp("2026-01-01").date()
            ),
            key="ins_from",
        )
        effective_to = st.date_input(
            "結束月（含；留空=至今）",
            value=(
                pd.to_datetime(selected["effective_to"]).date()
                if selected and selected.get("effective_to") else None
            ),
            key="ins_to",
        )

    note = st.text_input(
        "備註",
        value=selected["note"] if selected and selected.get("note") else "",
        key="ins_note",
    )

    col_save, col_del = st.columns(2)
    with col_save:
        if st.button("💾 儲存", type="primary", key="ins_save"):
            payload = {
                "clinic_id": clinic_id,
                "doctor_id": doctor_id,
                "insurance_base": insurance_base,
                "labor_deduction": labor_deduction,
                "nhi_deduction": nhi_deduction,
                "effective_from": str(effective_from),
                "effective_to": str(effective_to) if effective_to else None,
                "note": note or None,
            }
            try:
                if is_edit:
                    sb.table("doctor_insurance_deductions").update(payload).eq("id", sid).execute()
                    st.success(f"✅ 已更新 id={sid}")
                else:
                    sb.table("doctor_insurance_deductions").insert(payload).execute()
                    st.success("✅ 已新增")
                st.rerun()
            except Exception as e:
                st.error(f"儲存失敗：{e}")
    with col_del:
        if is_edit and st.button("🗑️ 刪除", key="ins_del"):
            try:
                sb.table("doctor_insurance_deductions").delete().eq("id", sid).execute()
                st.success(f"✅ 已刪除 id={sid}")
                st.rerun()
            except Exception as e:
                st.error(f"刪除失敗：{e}")


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
    months_sorted = sorted(months_set, reverse=True)
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
                    "原因：該品項在「自費商品成本&售價」或「科中進貨價目表」中找不到對應。"
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
