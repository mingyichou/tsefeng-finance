"""
Cookie 持久化 — 暫時 disabled

原因：streamlit-cookies-manager 0.2.0（2022 年套件）與 streamlit 1.57 不相容，
其內部仍使用已 deprecated 的 st.cache，造成 component hydrate 卡住、頁面空白。

TODO（之後重做）：改用 extra-streamlit-components.CookieManager
+ cryptography.fernet 自行加密 cookie payload，達成 24 小時免重登。

當前行為：每次重整 / 關分頁都要重新 OTP（直到 cookie 持久化重做完成）。
"""

from typing import Optional


def get_cookie_manager():
    """Stub: 永遠回傳 True，讓 main() 流程不會被卡住"""
    return True


def save_session_to_cookie(session_data: dict) -> None:
    """Stub: no-op"""
    pass


def load_session_from_cookie() -> Optional[dict]:
    """Stub: 永遠回 None，讓 try_restore_from_cookie 直接走「未登入」分支"""
    return None


def clear_session_cookie() -> None:
    """Stub: no-op"""
    pass
