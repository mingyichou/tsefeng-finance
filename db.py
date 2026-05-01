"""
Supabase 連線模組

設計：
- get_authed_client 每個 streamlit session 維護一個帶 token 的 client
- access_token 不變的 rerun 不重打 Auth API（避免 set_session 的 ReadTimeout）
- set_session 失敗時 fallback 到 postgrest.auth() 純 header 模式
"""

import streamlit as st
from supabase import create_client, Client


def _build_unauthed_client() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["publishable_key"]
    return create_client(url, key)


@st.cache_resource
def get_supabase_client() -> Client:
    """
    匿名 client（給未登入流程用，例如 OAuth callback、login）。
    cache_resource 跨 session 共用（OK，沒帶 user token）。
    """
    return _build_unauthed_client()


def get_authed_client() -> Client:
    """
    取得已帶入登入 session 的 client。

    優化：
      - 每個 streamlit session 在 session_state 快取自己的 client
      - access_token 沒變的 rerun 直接用 cached，不重打 Supabase Auth API
      - set_session 失敗時退到 postgrest.auth(token) 純 header 模式
    """
    sess = st.session_state.get("session")
    if not sess:
        return get_supabase_client()

    cur_token = sess.get("access_token")
    cached_token = st.session_state.get("_authed_token")
    cached_client = st.session_state.get("_authed_client")

    if cached_client is not None and cached_token == cur_token:
        return cached_client

    # token 變了或第一次：新建 client（per-session，避免跨 user 污染）
    sb = _build_unauthed_client()
    refresh_token = sess.get("refresh_token", "")
    try:
        sb.auth.set_session(cur_token, refresh_token)
    except Exception as e:
        # set_session 內部會打 /auth/v1/user 驗證，可能 ReadTimeout。
        # Fallback：直接把 token 設到 PostgREST header，跳過驗證
        try:
            sb.postgrest.auth(cur_token)
        except Exception:
            pass
        # 提示但不阻塞 — 多數情況下 token 還是有效的
        st.toast(f"⚠️ Auth API 慢，改用 token-only 模式（{type(e).__name__}）", icon="⚠️")

    st.session_state._authed_client = sb
    st.session_state._authed_token = cur_token
    return sb
