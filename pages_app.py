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
    st.info("🚧 開發中（Phase 2：依檔案類型逐一加入上傳區塊）")

    st.markdown("**將支援 14 種資料來源上傳：**")
    st.markdown("""
    1. 銀行對帳：玉山健保戶 CSV、中信進出戶 PDF
    2. 健保收入：醫療費用付款通知書 HTML（批次）
    3. 醫師業績：門診統計、看診人數、合理門診量、自費統計
    4. 診所支出：現金、合約、支票、調貨
    5. 薪資與商品：薪資表、商品成本售價
    6. 手 KEY：額外收入、非常規收支
    """)


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
