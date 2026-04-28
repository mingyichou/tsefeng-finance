"""
Supabase 連線模組
"""

from supabase import create_client, Client
import streamlit as st


@st.cache_resource
def get_supabase_client() -> Client:
    """
    取得 Supabase client（使用 publishable key，受 RLS 控制）。
    cache_resource 確保整個 session 共用同一個 client，避免重複建立。
    """
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["publishable_key"]
    return create_client(url, key)


def get_authed_client() -> Client:
    """
    取得已帶入登入 session 的 client。
    Supabase Python SDK 會自動把 session 的 access_token 加到 request header，
    讓 RLS policy 能透過 auth.uid() 識別當前使用者。
    """
    sb = get_supabase_client()
    if "session" in st.session_state and st.session_state.session:
        # 把 session 設回 client，使後續 query 帶上 auth header
        sb.auth.set_session(
            st.session_state.session["access_token"],
            st.session_state.session["refresh_token"],
        )
    return sb
