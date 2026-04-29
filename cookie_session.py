"""
Cookie 持久化 — 24 小時內免重登

用 extra-streamlit-components.CookieManager 處理 client-side cookie 讀寫
+ cryptography.fernet 自己加密 payload，避免 token 明文曝露

流程：
- 登入成功 → save_session_to_cookie(session) → 加密寫入 cookie
- F5 / 關分頁再開 → main() 呼叫 try_restore_from_cookie → 讀 cookie 解密還原
- 24 小時 TTL（每次活動更新 saved_at，sliding window）
- 主動登出 / 過期 → clear_session_cookie

注意：extra-streamlit-components 的 CookieManager 第一次 page load
會 render component iframe 觸發 client side 讀 cookie，需 1 個 rerun 才能拿到值。
"""

import json
import time
import base64
import hashlib
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st
import extra_streamlit_components as stx
from cryptography.fernet import Fernet, InvalidToken


COOKIE_NAME = "tsefeng_finance_session"
TTL_SECONDS = 24 * 60 * 60  # 24 小時


@st.cache_resource
def _get_fernet() -> Fernet:
    """從 secrets.cookies.password 衍生 32-byte URL-safe fernet key"""
    pwd = st.secrets["cookies"]["password"].encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(pwd).digest())
    return Fernet(key)


def get_cookie_manager() -> stx.CookieManager:
    """取得 cookie manager 實例（存在 session_state 跨 rerun 共用）"""
    if "_cookie_mgr" not in st.session_state:
        st.session_state._cookie_mgr = stx.CookieManager(key="cookie_mgr")
    return st.session_state._cookie_mgr


def save_session_to_cookie(session_data: dict) -> None:
    """加密後寫入 cookie，24 小時後過期"""
    try:
        cm = get_cookie_manager()
        fernet = _get_fernet()
        payload = json.dumps({**session_data, "saved_at": int(time.time())})
        encrypted = fernet.encrypt(payload.encode()).decode()
        expires = datetime.now() + timedelta(seconds=TTL_SECONDS)
        # extra-streamlit-components 的 set 需要 unique key 避免 streamlit 重複元件警告
        cm.set(
            COOKIE_NAME,
            encrypted,
            expires_at=expires,
            key=f"set_cookie_{int(time.time() * 1000)}",
        )
    except Exception:
        # 寫入失敗不影響登入主流程
        pass


def load_session_from_cookie() -> Optional[dict]:
    """讀 cookie 並解密；失敗或過期回 None"""
    try:
        cm = get_cookie_manager()
        encrypted = cm.get(cookie=COOKIE_NAME)
        if not encrypted:
            return None
        fernet = _get_fernet()
        decrypted = fernet.decrypt(encrypted.encode()).decode()
        data = json.loads(decrypted)
        # TTL 檢查
        if time.time() - data.get("saved_at", 0) > TTL_SECONDS:
            clear_session_cookie()
            return None
        return data
    except (InvalidToken, json.JSONDecodeError, KeyError):
        # 加密金鑰變了或 payload 損毀 → 清掉重來
        clear_session_cookie()
        return None
    except Exception:
        return None


def clear_session_cookie() -> None:
    """登出時清掉 cookie"""
    try:
        cm = get_cookie_manager()
        cm.delete(
            COOKIE_NAME,
            key=f"del_cookie_{int(time.time() * 1000)}",
        )
    except Exception:
        pass
