"""
Supabase Auth — 6 位數 OTP 驗證碼登入 + 白名單
（採 OTP 而非 Magic Link，避免 Streamlit 後端看不到 URL hash 的問題）
"""

import streamlit as st
from db import get_supabase_client, get_authed_client


def show_login_page():
    """登入頁：兩階段 — 先輸入 Email → 再輸入 6 位數 OTP"""
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

        # 階段判斷：是否已寄出 OTP
        if "otp_sent_to" not in st.session_state:
            _show_email_step()
        else:
            _show_otp_step()


def _show_email_step():
    """階段 1：輸入 Email 並寄送 OTP"""
    with st.form("email_form"):
        email = st.text_input(
            "Email",
            placeholder="emperorchou@gmail.com",
            help="輸入授權 Email，系統將寄送 6 位數驗證碼",
        )
        submitted = st.form_submit_button(
            "📧 寄送驗證碼", use_container_width=True, type="primary"
        )

        if submitted:
            if not email or "@" not in email:
                st.warning("請輸入有效的 Email")
            else:
                _send_otp(email.strip().lower())


def _send_otp(email: str):
    """寄送 OTP 驗證碼到指定 Email"""
    try:
        sb = get_supabase_client()
        sb.auth.sign_in_with_otp(
            {
                "email": email,
                "options": {
                    "should_create_user": False,  # 不允許自助註冊（限白名單）
                },
            }
        )
        st.session_state.otp_sent_to = email
        st.rerun()
    except Exception as e:
        st.error(f"寄送失敗：{e}")


def _show_otp_step():
    """階段 2：輸入 OTP 驗證碼（長度由 Supabase 後台設定，預設 6~8 位）"""
    email = st.session_state.otp_sent_to
    st.success(f"✅ 已寄送驗證碼到 **{email}**")
    st.caption("請打開信箱並複製驗證碼（5 分鐘內有效）。如沒收到請檢查垃圾信箱。")

    with st.form("otp_form"):
        otp = st.text_input(
            "驗證碼",
            placeholder="例如 81971273",
            max_chars=10,
            help="從 Email 複製過來貼上",
        )
        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button(
                "🔓 登入", use_container_width=True, type="primary"
            )
        with col2:
            cancel = st.form_submit_button("← 換 Email", use_container_width=True)

        if cancel:
            del st.session_state.otp_sent_to
            st.rerun()

        if submitted:
            otp_clean = otp.strip() if otp else ""
            if not otp_clean or not otp_clean.isdigit() or len(otp_clean) < 6:
                st.warning("請輸入正確的驗證碼（至少 6 位數字）")
            else:
                _verify_otp(email, otp_clean)


def _verify_otp(email: str, otp: str):
    """驗證 OTP 並建立 session"""
    try:
        sb = get_supabase_client()
        response = sb.auth.verify_otp(
            {"email": email, "token": otp, "type": "email"}
        )
        if response and response.session and response.user:
            st.session_state.session = {
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "user_id": response.user.id,
                "email": response.user.email,
            }
            del st.session_state.otp_sent_to
            st.rerun()
        else:
            st.error("驗證失敗，請重試")
    except Exception as e:
        st.error(f"驗證失敗：{e}")


def check_whitelist(user_id: str) -> dict | None:
    """檢查使用者是否在 allowed_users 白名單（用帶 session 的 client，RLS 才能識別 auth.uid）"""
    try:
        sb = get_authed_client()
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
    for key in ["session", "user_role", "otp_sent_to"]:
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
