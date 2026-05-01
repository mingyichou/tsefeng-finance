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
    st.info("🚧 開發中（Phase 3：醫師業績圓餅圖、12 個月柱狀圖、收入結構）")

    sb = get_authed_client()
    try:
        clinics = sb.table("clinics").select("*").execute()
        doctors = sb.table("doctors").select("*").execute()
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("診所主檔")
            st.dataframe(pd.DataFrame(clinics.data), use_container_width=True)
        with col2:
            st.subheader("醫師主檔")
            st.dataframe(pd.DataFrame(doctors.data), use_container_width=True)
    except Exception as e:
        st.error(f"資料庫連線測試失敗：{e}")


# ============================================================
# 2. 收支總覽（Phase 2 + 4）
# ============================================================
def page_overview():
    st.title("💰 收支總覽")
    st.info("🚧 開發中（Phase 2 匯入 + Phase 4 權責還原後此頁顯示月度損益）")

    st.markdown("**本頁將顯示：**")
    st.markdown("""
    - 月度損益表（收入細項 vs 支出細項）
    - 健保 vs 自費收入結構
    - 兩家診所合併與分開檢視
    - 12 個月趨勢
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

    # ─── 其他類型（待實作）───────────────────────────
    st.divider()
    st.markdown("**🚧 其他資料來源（待實作）：**")
    st.markdown("""
    - 合理門診量
    - 薪資表、商品成本售價、@科中進貨價目表
    - 手 KEY：額外收入、非常規收支
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
            resp = (
                sb.table("nhi_payment_notices")
                .upsert(
                    batch,
                    on_conflict="source_filename",
                    ignore_duplicates=True,
                )
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
        st.warning("⚠️ 尚無醫師看診人數資料（doctor_visit_stats 為空），請先到「本月資料匯入」上傳。")
        return

    col1, col2 = st.columns([2, 5])
    with col1:
        service_month = st.selectbox(
            "服務月份",
            months_set,
            format_func=lambda d: f"{d[:7]}",
            key="salary_month",
        )

    # ─── 計算 ───
    with st.spinner("計算中..."):
        components, payslips = run_salary_calculation(sb, service_month)

    if not components:
        st.warning("該月份無計算結果（doctor_clinic 表可能為空）")
        return

    # ─── 主聘月薪結構 ───
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
        pd.DataFrame(pay_rows),
        use_container_width=True,
        hide_index=True,
    )

    # ─── 分診所明細 ───
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

    # ─── 複針/A91 獎金細項（4月起）───
    has_acu = any(c.acu_complex_bonus or c.a91_bonus for c in components)
    if has_acu:
        with st.expander("💉 複針/A91 獎金細項（115/04 起新制）"):
            rows = []
            for c in sorted(components, key=lambda x: (x.doctor_name, x.clinic_name)):
                if not (c.acu_complex_bonus or c.a91_bonus
                        or c.acu_complex_mid_count or c.a91_count):
                    continue
                rows.append({
                    "診所": c.clinic_name,
                    "醫師": c.doctor_name,
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
                st.caption(
                    "公式：中複針 ×20 + 高複針 ×40 + A91 ×14（115/04 起套用）"
                )

    # ─── 跨支援墊付 ───
    cross = [p for p in payslips if p.support_clinic_id and p.gross_support > 0]
    if cross:
        with st.expander("💱 跨支援墊付（豐沛金流項目）"):
            st.caption(
                "看診診所還主聘診所；薪資由主聘診所統一發給醫師，"
                "後續透過豐沛金流結算"
            )
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

    # ─── 自費抽成明細 ───
    with st.expander("💰 自費抽成各項目明細"):
        rows = []
        for c in sorted(components, key=lambda x: (x.doctor_name, x.clinic_name)):
            row = {
                "診所": c.clinic_name,
                "醫師": c.doctor_name,
            }
            row.update(c.commission_breakdown)
            row["合計"] = c.commission_total
            rows.append(row)
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True
        )

    # ─── 業績獎金明細（觸發者）───
    triggered = [c for c in components if c.perf_triggered]
    if triggered:
        with st.expander("🎯 業績獎金明細（觸發者）"):
            rows = [
                {
                    "診所": c.clinic_name,
                    "醫師": c.doctor_name,
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

    # ─── 寫入 DB ───
    st.divider()
    if not st.session_state.get("edit_mode"):
        st.info(
            "上方為即時試算結果，未寫入 DB。如需寫入 doctor_salary_monthly，"
            "請啟用左下「編輯模式」後再回到此頁。"
        )
        return

    st.warning(
        "⚠️ 寫入 DB 會覆蓋同月份既有計算結果（依 clinic+doctor+service_month UNIQUE）"
    )
    if st.button(
        f"💾 寫入 {service_month[:7]} 計算結果到 doctor_salary_monthly",
        type="primary",
        key=f"salary_save_{service_month}",
    ):
        try:
            n = upsert_salary_monthly(sb, components, payslips)
            st.success(f"✅ 寫入 {n} 筆")
            st.balloons()
        except Exception as e:
            st.error(f"寫入失敗：{e}")


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
