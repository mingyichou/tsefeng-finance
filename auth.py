"""
Supabase Auth Magic Link + 白名單驗證
"""

import streamlit as st
from db import get_supabase_client


def show_login_page():
    """顯示登入頁面：輸入 Email → 寄 Magic Link"""
    col1, col2, col3 = st.columns([1, 1.5, 1])

    with col2:
        st.markdown("")
        st.markdown("")
        st.markdown(
            "<h1 style='text-align:center; color:#6A5ACD;'>🏥 澤豐聯盟</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<h3 style='text-align:center; color:#888;'>財務與業績系統</h3>",
            unsafe_allow_html=True,
        )
        st.markdown("")

        with st.form("magic_link_form"):
            email = st.text_input(
                "Email",
                placeholder="emperorchou@gmail.com",
                help="輸入授權 Email，系統將寄送一次性登入連結",
            )
            submitted = st.form_submit_button(
                "📧 寄送登入連結", use_container_width=True, type="primary"
            )

            if submitted:
                if not email or "@" not in email:
                    st.warning("請輸入有效的 Email")
                else:
                    send_magic_link(email)


def send_magic_link(email: str):
    """寄送 Magic Link 到指定 Email"""
    try:
        sb = get_supabase_client()
        site_url = st.secrets["app"]["site_url"]
        sb.auth.sign_in_with_otp(
            {
                "email": email,
                "options": {"email_redirect_to": site_url},
            }
        )
        st.success(
            f"✅ 已寄送登入連結到 **{email}**\n\n"
            "請打開信箱並點擊連結（5 分鐘內有效）"
        )
        st.info("💡 如沒收到，請檢查垃圾信箱")
    except Exception as e:
        st.error(f"寄送失敗：{e}")


def handle_auth_callback():
    """
    處理 Magic Link 回調：從 URL query params 讀 code → 換 session
    Magic Link 連結格式：https://your-app.com/?code=xxxxx
    """
    code = st.query_params.get("code")
    if code and "session" not in st.session_state:
        try:
            sb = get_supabase_client()
            response = sb.auth.exchange_code_for_session({"auth_code": code})
            if response and response.session:
                st.session_state.session = {
                    "access_token": response.session.access_token,
                    "refresh_token": response.session.refresh_token,
                    "user_id": response.user.id,
                    "email": response.user.email,
                }
                # 清掉 URL 上的 code，避免重複交換
                st.query_params.clear()
                st.rerun()
        except Exception as e:
            st.error(f"登入驗證失敗：{e}")
            st.query_params.clear()


def check_whitelist(user_id: str) -> dict | None:
    """
    檢查使用者是否在 allowed_users 白名單。
    回傳：{ email, role } 或 None（不在白名單）
    """
    try:
        sb = get_supabase_client()
        # 用 service_role 是不必要的；這裡 RLS 應允許自己讀自己的 record
        # 但安全起見可在 schema 加 policy: 允許自己讀 allowed_users 自己的 row
        resp = (
            sb.table("allowed_users")
            .select("email, role")
            .eq("user_id", user_id)
            .execute()
        )
        if resp.data:
            return resp.data[0]
        return None
    except Exception as e:
        st.error(f"白名單查詢失敗：{e}")
        return None


def sign_out():
    """登出"""
    try:
        sb = get_supabase_client()
        sb.auth.sign_out()
    except Exception:
        pass
    for key in ["session", "user_role"]:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()


def is_logged_in() -> bool:
    """是否已登入且通過白名單"""
    return (
        "session" in st.session_state
        and st.session_state.session is not None
        and st.session_state.get("user_role") is not None
    )
