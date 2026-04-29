"""
澤豐聯盟財務與業績系統 — 主入口
"""

import streamlit as st

st.set_page_config(
    page_title="澤豐聯盟 財務系統",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 全域 CSS
st.markdown(
    """
<style>
    .sidebar-title {
        font-size: 1.3rem;
        font-weight: 700;
        color: #6A5ACD;
        margin-bottom: 0.5rem;
    }
    .edit-warning {
        font-size: 0.85rem;
        color: #b85c00;
    }
</style>
""",
    unsafe_allow_html=True,
)

from auth import (
    show_login_page,
    check_whitelist,
    sign_out,
    is_logged_in,
)
from pages_app import (
    page_dashboard,
    page_overview,
    page_import,
    page_personal,
    page_settings,
)


def main():
    # 未登入：顯示登入頁（OTP 驗證碼流程）
    if "session" not in st.session_state or st.session_state.get("session") is None:
        show_login_page()
        return

    # 3. 已登入但尚未驗證白名單
    if "user_role" not in st.session_state:
        user_id = st.session_state.session["user_id"]
        role_info = check_whitelist(user_id)
        if role_info is None:
            st.error(
                f"❌ 您的 Email（{st.session_state.session['email']}）"
                "不在系統白名單，無法存取。請聯絡院長。"
            )
            if st.button("登出"):
                sign_out()
            return
        st.session_state.user_role = role_info

    # 4. 已通過白名單 → 顯示主介面
    render_main_app()


def render_main_app():
    role_info = st.session_state.user_role
    email = st.session_state.session["email"]

    with st.sidebar:
        st.markdown(
            '<p class="sidebar-title">🏥 澤豐聯盟</p>', unsafe_allow_html=True
        )
        st.caption(f"👤 {email}（{role_info['role']}）")
        st.divider()

        menu_items = [
            "📊 業績與財務儀表板",
            "💰 收支總覽",
            "📥 本月資料匯入區",
            "💸 院長個人財富分析",
            "⚙️ 系統設定",
            "🚪 登出",
        ]
        choice = st.radio("功能", menu_items, label_visibility="collapsed")

        # 編輯模式鎖（最底部）
        st.divider()
        edit_mode = st.checkbox(
            "🔓 啟用編輯模式",
            value=False,
            help="預設唯讀以防誤觸。需上傳/修改資料時請勾選。",
            key="edit_mode",
        )
        if edit_mode:
            st.markdown(
                '<p class="edit-warning">⚠️ 編輯模式啟用中</p>',
                unsafe_allow_html=True,
            )

    # 路由
    if choice == "🚪 登出":
        sign_out()
    elif choice == "📊 業績與財務儀表板":
        page_dashboard()
    elif choice == "💰 收支總覽":
        page_overview()
    elif choice == "📥 本月資料匯入區":
        page_import()
    elif choice == "💸 院長個人財富分析":
        page_personal()
    elif choice == "⚙️ 系統設定":
        page_settings()


if __name__ == "__main__":
    main()
