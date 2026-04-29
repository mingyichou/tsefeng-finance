"""
加密 cookie session 持久化
登一次後 24 小時內免重登（用 refresh_token 自動換新 access_token）
"""

import json
import time
from typing import Optional

import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager


COOKIE_NAME = "session"
COOKIE_PREFIX = "tsefeng_finance/"
SESSION_TTL_SECONDS = 24 * 60 * 60  # 24 小時


def get_cookie_manager() -> Optional[EncryptedCookieManager]:
    """取得已 ready 的 cookie manager；若 cookies 尚未載入回 None"""
    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = EncryptedCookieManager(
            prefix=COOKIE_PREFIX,
            password=st.secrets["cookies"]["password"],
        )
    cm = st.session_state.cookie_manager
    if not cm.ready():
        return None
    return cm


def save_session_to_cookie(session_data: dict) -> None:
    """登入成功後寫 session 到加密 cookie"""
    cm = get_cookie_manager()
    if cm is None:
        return
    payload = {**session_data, "saved_at": int(time.time())}
    cm[COOKIE_NAME] = json.dumps(payload)
    cm.save()


def load_session_from_cookie() -> Optional[dict]:
    """讀 cookie 回 session dict；過期/無效回 None"""
    cm = get_cookie_manager()
    if cm is None:
        return None
    raw = cm.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        # 檢查 TTL
        saved_at = data.get("saved_at", 0)
        if time.time() - saved_at > SESSION_TTL_SECONDS:
            clear_session_cookie()
            return None
        return data
    except (json.JSONDecodeError, KeyError):
        clear_session_cookie()
        return None


def clear_session_cookie() -> None:
    """登出時清 cookie"""
    cm = get_cookie_manager()
    if cm is None:
        return
    if COOKIE_NAME in cm:
        del cm[COOKIE_NAME]
        cm.save()
