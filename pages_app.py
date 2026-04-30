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

    # ─── 其他類型（待實作）───────────────────────────
    st.divider()
    st.markdown("**🚧 其他資料來源（待實作）：**")
    st.markdown("""
    - 門診申報金額統計報表 / 看診人數+初診 / 合理門診量 / 自費統計
    - 澤沛 A91+複針
    - 診所支出：現金、合約、支票、調貨
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
# 4. 院長個人透支（Phase 5）
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

    tab1, tab2, tab3 = st.tabs(["白名單使用者", "醫師主檔", "系統資訊"])

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

    with tab3:
        st.subheader("系統資訊")
        st.text(f"登入者：{st.session_state.session.get('email')}")
        st.text(f"角色：{st.session_state.get('user_role', {}).get('role', 'unknown')}")
        st.text(f"User ID：{st.session_state.session.get('user_id')}")
        st.caption("Supabase URL：" + st.secrets["supabase"]["url"])
