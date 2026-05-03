"""
Supabase Auth — 主要採 Google OAuth，備用 Email OTP

主流程：
1. 使用者點「用 Google 登入」 → sb.auth.sign_in_with_oauth(google) → redirect 到 Google
2. Google 認證後 → Supabase callback → redirect 回 site_url?code=xxx
3. main() 偵測 query_params['code'] → handle_oauth_callback → exchange_code_for_session
4. 寫入 st.session_state.session

備用：Email OTP（避開 email rate limit 用，但仍受 2/h 限制）
"""

import streamlit as st
import streamlit.components.v1 as components
from db import get_supabase_client, get_authed_client
from cookie_session import (
    save_session_to_cookie,
    load_session_from_cookie,
    clear_session_cookie,
    get_cookie_manager,
)


def show_login_page():
    """登入頁：Google OAuth 為主，Email OTP 為備援"""
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
        st.markdown("")

        # 主流程：Google OAuth — 開頁時生成 URL，render 成可點 link button
        if "google_oauth_url" not in st.session_state:
            _prepare_google_oauth_url()

        if st.session_state.get("google_oauth_url"):
            # 用 st.markdown 直接渲染 <a>（在 streamlit 主 frame，無 sandbox 限制）
            # components.html 的 iframe 有 sandbox 不允許 target=_top 導航
            oauth_url = st.session_state.google_oauth_url
            st.markdown(
                f"""
                <style>
                  .google-signin-btn {{
                    display: block;
                    width: 100%;
                    padding: 0.6rem 1rem;
                    background-color: #6A5ACD;
                    color: white !important;
                    text-decoration: none !important;
                    text-align: center;
                    border-radius: 0.5rem;
                    font-weight: 500;
                    font-size: 1rem;
                    margin: 0.5rem 0;
                    box-sizing: border-box;
                    transition: background-color 0.15s;
                  }}
                  .google-signin-btn:hover {{
                    background-color: #5848B6;
                    color: white !important;
                  }}
                </style>
                <a href="{oauth_url}" target="_blank" rel="noopener noreferrer" class="google-signin-btn">
                  🔐 使用 Google 帳號登入
                </a>
                """,
                unsafe_allow_html=True,
            )
            st.caption(
                "點上方按鈕會在**新分頁**開啟 Google 登入。完成後新分頁顯示主畫面，可關閉此分頁。"
            )
            # ⚠️ 已知問題：剛 git push 觸發 Streamlit Cloud 重新部署期間登入，
            # 會發生 PKCE code_verifier mismatch（cache_resource 失效）。
            # 解法待實作：自管 PKCE — verifier 存 supabase oauth_pending table，
            # callback 用 state_id 查回後手動 exchange。
            # 暫時 workaround：部署完成後（約 30-60 秒）再登入。
        else:
            st.error("Google 登入連結建立失敗，請改用 Email OTP")

        st.markdown("")

        # 備用：Email OTP
        with st.expander("使用 Email 驗證碼登入（備用）"):
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
    """驗證 OTP 並建立 session（同時寫加密 cookie，24 小時內免重登）"""
    try:
        sb = get_supabase_client()
        response = sb.auth.verify_otp(
            {"email": email, "token": otp, "type": "email"}
        )
        if response and response.session and response.user:
            session_data = {
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token,
                "user_id": response.user.id,
                "email": response.user.email,
            }
            st.session_state.session = session_data
            save_session_to_cookie(session_data)
            del st.session_state.otp_sent_to
            st.rerun()
        else:
            st.error("驗證失敗，請重試")
    except Exception as e:
        st.error(f"驗證失敗：{e}")


def _gen_pkce_pair() -> tuple[str, str]:
    """生成 PKCE (verifier, challenge)。verifier 是 43-128 字元 URL-safe；
    challenge = base64url(sha256(verifier))。"""
    import secrets, hashlib, base64
    verifier = secrets.token_urlsafe(48)  # 64 chars URL-safe
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def _prepare_google_oauth_url():
    """
    自管 PKCE 版本：
    - 自己生成 verifier + challenge
    - verifier 存 supabase oauth_pending table（state_id 為 key）
    - 構造 supabase OAuth URL，redirect_to 帶 state_id
    - callback 從 query_params 取 state_id 後查 verifier 手動 exchange

    解 streamlit cache_resource 在 worker 重啟/跨分頁時 verifier 遺失問題。
    """
    import secrets
    from urllib.parse import quote

    try:
        sb = get_supabase_client()
        site_url = st.secrets["app"]["site_url"]
        supabase_url = st.secrets["supabase"]["url"]

        state_id = secrets.token_urlsafe(16)  # 128 bits random
        verifier, challenge = _gen_pkce_pair()

        # 寫進 oauth_pending（用後即刪）
        sb.table("oauth_pending").insert({
            "state_id": state_id,
            "code_verifier": verifier,
        }).execute()

        # 構造 Supabase OAuth URL
        # redirect_to 帶我們自己的 state_id，supabase 會原樣回傳
        redirect_to = f"{site_url}?state_id={state_id}"
        oauth_url = (
            f"{supabase_url}/auth/v1/authorize"
            f"?provider=google"
            f"&redirect_to={quote(redirect_to, safe='')}"
            f"&code_challenge={challenge}"
            f"&code_challenge_method=S256"
        )
        st.session_state.google_oauth_url = oauth_url
    except Exception as e:
        st.error(f"Google 登入連結準備失敗：{e}")
        st.session_state.google_oauth_url = None


def handle_oauth_callback(code: str):
    """
    自管 PKCE 版本：
    1. 從 query_params 取 state_id
    2. 查 oauth_pending 取對應 verifier
    3. 手動 POST /auth/v1/token?grant_type=pkce 兌換 access_token
    4. 寫 session_state + 清掉 oauth_pending
    """
    import httpx
    try:
        state_id = st.query_params.get("state_id")
        if not state_id:
            st.error("OAuth callback 缺少 state_id（PKCE 自管必須）")
            st.query_params.clear()
            return

        sb = get_supabase_client()
        resp = (
            sb.table("oauth_pending")
            .select("code_verifier")
            .eq("state_id", state_id)
            .execute()
        )
        if not resp.data:
            st.error("OAuth state 已失效或不存在（>10 分鐘自動清理），請重新登入")
            st.query_params.clear()
            return

        verifier = resp.data[0]["code_verifier"]

        # 手動兌換 token
        supabase_url = st.secrets["supabase"]["url"]
        anon_key = st.secrets["supabase"]["publishable_key"]
        r = httpx.post(
            f"{supabase_url}/auth/v1/token?grant_type=pkce",
            json={"auth_code": code, "code_verifier": verifier},
            headers={
                "apikey": anon_key,
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        if r.status_code != 200:
            st.error(f"OAuth 兌換失敗 ({r.status_code})：{r.text[:300]}")
            st.query_params.clear()
            return

        token_data = r.json()
        user = token_data.get("user") or {}

        # 用後即刪 oauth_pending（防重用）
        try:
            sb.table("oauth_pending").delete().eq("state_id", state_id).execute()
        except Exception:
            pass

        session_data = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "user_id": user.get("id"),
            "email": user.get("email"),
        }
        st.session_state.session = session_data
        save_session_to_cookie(session_data)  # stub no-op
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"OAuth 兌換失敗：{e}")
        st.query_params.clear()


def try_restore_from_cookie() -> bool:
    """
    開頁時 cold start 嘗試從 cookie 還原 session。
    回傳 True 代表成功還原（後續 main() 直接走已登入分支）。
    """
    # 已經有 session 不必還原
    if "session" in st.session_state and st.session_state.get("session"):
        return True

    # 從 cookie 讀
    cookie_data = load_session_from_cookie()
    if not cookie_data:
        return False

    # 用 refresh_token 換新 access_token（避免 access_token 早過期）
    try:
        sb = get_supabase_client()
        sb.auth.set_session(
            cookie_data["access_token"],
            cookie_data["refresh_token"],
        )
        # supabase-py 內部會自動 refresh，取最新 session
        new_session = sb.auth.get_session()
        if new_session and new_session.access_token:
            session_data = {
                "access_token": new_session.access_token,
                "refresh_token": new_session.refresh_token,
                "user_id": cookie_data["user_id"],
                "email": cookie_data["email"],
            }
            st.session_state.session = session_data
            # 重新寫 cookie 更新 saved_at（sliding window）
            save_session_to_cookie(session_data)
            return True
    except Exception:
        # refresh 失敗（cookie 過期或 token 失效）→ 清掉
        clear_session_cookie()

    return False


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
    """登出（同時清 cookie）"""
    try:
        sb = get_supabase_client()
        sb.auth.sign_out()
    except Exception:
        pass
    clear_session_cookie()
    for key in ["session", "user_role", "otp_sent_to", "google_oauth_url"]:
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
