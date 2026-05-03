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
        st.dataframe(
            detail.sort_values(["月份", "診所", "醫師"]),
            use_container_width=True, hide_index=True,
        )


# ============================================================
# 2. 收支總覽（Phase 4 月度損益）
# ============================================================
def page_overview():
    st.title("💰 月度實帳收支總覽")

    from data_processor.monthly_pl import (
        calculate_both_clinics, list_available_months,
    )

    st.caption(
        "🗓️ **實帳模式**：每筆按實際入帳/出帳月份歸屬（不是業績服務月）。"
        "業績按服務月歸屬請見「業績儀表板」頁。"
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

    # ─── KPI 卡片 ───
    st.divider()
    total_income = pl_fz.total_income + pl_fp.total_income
    total_expense = pl_fz.total_expense + pl_fp.total_expense
    total_net = total_income - total_expense

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("總收入", f"${total_income:,}")
    k2.metric("總支出", f"${total_expense:,}")
    k3.metric("淨利", f"${total_net:,}",
              delta=f"{total_net/total_income:.1%}" if total_income else "N/A")
    k4.metric(
        "澤豐中信餘額（月末）",
        f"${pl_fz.x11_current_balance:,}",
        delta=f"從 {pl_fz.x1_prev_balance:,} → {pl_fz.x11_current_balance:,}",
    )

    # ─── 澤沛實帳 ───
    st.divider()
    st.subheader("🏥 澤沛實帳")
    st.caption("玉山健保戶 + 中信進出戶逐筆按月聚合。")

    fp_rows = [
        ("收入", "玉山健保入帳", pl_fp.nhi_inflow),
        ("收入", "中信跨診所匯入", pl_fp.cross_inflow),
        ("收入", "中信現金存入", pl_fp.cash_deposit),
        ("收入", "其他入帳", pl_fp.other_income),
        ("收入", "非常規收入(手KEY)", pl_fp.misc_income),
        ("收入", "▶ 收入合計", pl_fp.total_income),
        ("支出", "員工薪資", pl_fp.staff_salary),
        ("支出", "醫師薪資", pl_fp.doctor_salary),
        ("支出", "現金支出彙總", pl_fp.cash_expense_total),
        ("支出", "合約支出彙總", pl_fp.contract_expense_total),
        ("支出", "非常規支出(手KEY)", pl_fp.misc_expense),
        ("支出", "▶ 支出合計", pl_fp.total_expense),
        ("盈餘", "▶ 淨利", pl_fp.net),
    ]
    st.dataframe(
        pd.DataFrame(fp_rows, columns=["類別", "項目", "金額"]),
        use_container_width=True, hide_index=True,
    )

    with st.expander("📑 澤沛玉山+中信銀行交易 raw"):
        st.caption("供院長對帳用。系統有歸不到類的會列在「其他」項。")

    # ─── 澤豐實帳（11+1 變數）───
    st.divider()
    st.subheader("🏥 澤豐實帳（12 變數規則）")
    st.caption(
        "x5/x6/x7/x8 是「下月入帳屬於前月」性質（豐沛金流來回款）。"
        "目前系統按入帳月份顯示，跨月歸屬需另一頁業績分析。"
    )

    fz_rows = [
        # 玉山
        ("玉山健保戶", "健保醫療給付入帳", pl_fz.nhi_inflow),
        ("玉山健保戶", "其他入帳", pl_fz.other_esun_in),
        ("玉山健保戶", "薪資轉帳支出", pl_fz.salary_outflow_esun),
        ("玉山健保戶", "健保/勞保代繳", pl_fz.nhi_premium_outflow),
        ("玉山健保戶", "其他支出", pl_fz.other_esun_out),
        # 中信 11 變數
        ("中信進出戶", "x1 前月餘額", pl_fz.x1_prev_balance),
        ("中信進出戶", "x2 玉山健保轉入", pl_fz.x2_zefeng_inflow),
        ("中信進出戶", "x5+x6+x7 澤沛來款（前月歸屬）", pl_fz.x6_fengpei_settle),
        ("中信進出戶", "x8 澤豐現金存入（前月歸屬）", pl_fz.x8_zefeng_cash_revenue),
        ("中信進出戶", "x10 手KEY 非常規收入", pl_fz.misc_income_x10),
        ("中信進出戶", "x10 手KEY 非常規支出", pl_fz.misc_expense_x10),
        ("中信進出戶", "x11 當月餘額", pl_fz.x11_current_balance),
        # 隱形支出
        ("隱形支出", "x3 澤豐現金支出", pl_fz.x3_zefeng_cash_expense),
        ("隱形支出", "x4 澤沛現金支出（澤豐代墊）", pl_fz.x4_zepei_cash_expense_proxy),
        ("隱形支出", "x9 編制外人力（謝松坊）", pl_fz.x9_offsite_staff_pay),
        ("隱形支出", "x12 澤豐合約支出", pl_fz.x12_zefeng_contract_expense),
        # 系統計算
        ("系統計算", "員工薪資（不含 x9）", pl_fz.staff_salary),
        ("系統計算", "醫師薪資", pl_fz.doctor_salary),
        # 合計
        ("合計", "▶ 總收入", pl_fz.total_income),
        ("合計", "▶ 總支出", pl_fz.total_expense),
        ("合計", "▶ 淨利", pl_fz.net),
    ]
    st.dataframe(
        pd.DataFrame(fz_rows, columns=["類別", "項目", "金額"]),
        use_container_width=True, hide_index=True,
    )

    # ─── 12 月趨勢圖 ───
    st.divider()
    st.subheader("📈 月度淨利趨勢")
    trend_data = []
    for m in sorted(months)[-12:]:
        try:
            tfz, tfp = calculate_both_clinics(sb, m)
            trend_data.append({
                "月份": m[:7],
                "澤豐淨利": tfz.net,
                "澤沛淨利": tfp.net,
                "合計": tfz.net + tfp.net,
            })
        except Exception:
            continue
    if trend_data:
        import altair as alt
        df_t = pd.DataFrame(trend_data)
        df_long = df_t.melt(
            id_vars=["月份"],
            value_vars=["澤豐淨利", "澤沛淨利", "合計"],
            var_name="診所", value_name="淨利",
        )
        chart = alt.Chart(df_long).mark_line(point=True).encode(
            x=alt.X("月份:N", sort="ascending"),
            y=alt.Y("淨利:Q"),
            color="診所:N",
            tooltip=["月份", "診所", alt.Tooltip("淨利:Q", format=",")],
        ).properties(height=350)
        st.altair_chart(chart, use_container_width=True)

    with st.expander("ℹ️ 計算規則說明"):
        st.markdown("""
**澤沛**（簡單）：玉山健保戶 + 中信進出戶逐筆按 `transaction_date` 月份聚合。

**澤豐**（12 變數）：
- 玉山健保戶逐筆 + 中信進出戶 11 變數推斷
- x3/x4/x9 是「中信看不到逐筆」的隱形支出，從其他 source 聚合
- x12 為澤豐合約支出（從 contract_expense 表）

**注意：** x5/x6/x7/x8 院長指示「下月入帳屬前月」邏輯尚未實作，
目前按實際入帳月份顯示。後續做業績分析頁時會用「跨月歸屬」邏輯。
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

    # ─── 現金支出 ────────────────────────────────────
    _section_cash_expense()

    st.divider()

    # ─── 合約支出 ────────────────────────────────────
    _section_contract_expense()

    st.divider()

    # ─── 支票支出（共用） ────────────────────────────
    _section_check_expense()

    st.divider()

    # ─── 調貨整理 ────────────────────────────────────
    _section_inventory_transfer()

    st.divider()

    # ─── 自費商品成本&售價 ─────────────────────────
    _section_self_pay_pricing()

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
    - 合理門診量
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
            f"上傳 {clinic_choice} 醫師自費統計（多份 xlsx）",
            type=["xlsx"],
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
            summaries.append({
                "檔名": f.name,
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
    ):
        _import_cash_records(sb, all_records)


def _section_cash_expense():
    """現金支出（Sprint 2.7a）— 年度累積檔，非按月"""
    from data_processor.expenses import parse_cash_expense

    st.subheader("💵 現金支出（年度累積檔）")
    st.caption(
        "檔名範例：『澤豐中醫診所現金支出.xlsx』、『澤沛中醫診所現金支出.xlsx』。"
        "檔內每列是一筆支出（月/日/描述/金額/備註）。"
    )

    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        clinic_choice = st.radio("診所", ["澤豐", "澤沛"], key="cash_exp_clinic")
    with col2:
        roc_year = st.number_input(
            "民國年", min_value=110, max_value=130, value=115, step=1,
            key="cash_exp_year",
            help="檔內 C0 是月份，年份要由此指定（檔名沒帶年）",
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
        records = parse_cash_expense(uploaded, uploaded.name, clinic_id, roc_year=int(roc_year))
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return

    if not records:
        st.warning("無可匯入的資料")
        return

    df = pd.DataFrame(records)
    st.success(f"✅ 解析 {len(records)} 筆")

    # 月份分組摘要
    df_sum = df.copy()
    df_sum["月份"] = df_sum["expense_date"].str[:7]
    summary = df_sum.groupby("月份", as_index=False).agg(
        筆數=("amount", "count"), 合計=("amount", "sum"),
    )
    st.markdown("**按月份彙總：**")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.markdown("**逐筆預覽：**")
    cols = ["expense_date", "description", "amount", "note"]
    st.dataframe(df[cols], use_container_width=True, height=300, hide_index=True)

    if st.button(
        f"💾 確認匯入 {clinic_choice} 現金支出（{len(records)} 筆）",
        type="primary",
        key=f"cash_exp_save_{clinic_choice}",
    ):
        try:
            sb.table("cash_expense").upsert(
                records, on_conflict="raw_row_hash", ignore_duplicates=True
            ).execute()
            st.success(f"✅ 寫入 {len(records)} 筆（重複 hash 自動跳過）")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


def _section_contract_expense():
    """合約支出（Sprint 2.7a）— 橫向月度表自動轉長表"""
    from data_processor.expenses import parse_contract_expense

    st.subheader("📜 合約支出（年度檔，橫向月度表）")
    st.caption(
        "檔名範例：『澤豐/澤沛中醫診所合約支出.xlsx』。系統把橫表轉成"
        "(月份 × 廠商) 的長表逐筆寫入 contract_expense。"
    )

    col1, col2 = st.columns([1, 4])
    with col1:
        clinic_choice = st.radio("診所", ["澤豐", "澤沛"], key="contract_exp_clinic")
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

    # 顯示目前 DB 狀態
    try:
        existing_count = len(
            sb.table("product_pricing").select("id").execute().data or []
        )
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


def _section_staff_salary():
    """員工薪資批次匯入（Sprint 2.8c）— 自動偵測最新 sheet + 跨診所代付辨識"""
    from data_processor.staff_salary import parse_staff_salary

    st.subheader("👤 員工薪資（自動偵測最新月份 sheet）")
    st.caption(
        "一個檔多個月 sheet。系統自動辨識最新月份；含「-更正」字尾優先採用。"
        "抓員工總額 + 跨診所代付（影響豐沛金流）。"
        "支援標題：「YYY年MM月薪資明細」、「YYY年MM月X薪資明細(Y代付)」。"
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

    try:
        sheet_name, records = parse_staff_salary(
            uploaded, uploaded.name, default_cid, short_to_cid
        )
    except Exception as e:
        st.error(f"解析失敗：{e}")
        return

    st.success(
        f"📋 自動偵測 sheet：**{sheet_name}**　|　解析 **{len(records)}** 位員工"
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

    if st.button(
        f"💾 確認匯入（{len(records)} 筆）",
        type="primary",
        key="staff_save",
    ):
        if not records:
            st.warning("無可匯入資料")
            return
        try:
            sb.table("staff_salary_summary").upsert(
                records,
                on_conflict="clinic_id,service_month,employee_label",
            ).execute()
            st.success(f"✅ 寫入 {len(records)} 筆")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


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
            # inventory_transfer 沒 UNIQUE constraint — 用 INSERT
            sb.table("inventory_transfer").insert(payload).execute()
            st.success(f"✅ 寫入 {len(payload)} 筆")
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

    tab1, tab2, tab_ins, tab3 = st.tabs(
        ["白名單使用者", "醫師主檔", "勞健保扣除額", "系統資訊"]
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
