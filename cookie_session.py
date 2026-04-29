"""
Cookie 持久化 — 暫時 disabled（再次）

問題歷程：
1. v1 用 streamlit-cookies-manager 0.2.0 → 與 streamlit 1.57 不相容（內部用 deprecated st.cache）
2. v2 用 extra-streamlit-components → 造成 supabase OAuth PKCE verifier
   在 storage 中遺失，登入失敗 "both auth code and code verifier should be non-empty"

可能原因：cookie manager 的 component init 觸發 streamlit rerun，
干擾到 supabase client 的 PKCE state 管理時序。

TODO（之後重做）：
- 改用純 components.html + vanilla JS 讀寫 browser cookie（不靠任何 cookie 套件）
- 自己 generate PKCE verifier/challenge，verifier 寫 cookie 跨 redirect 持久化
- 或：完全捨棄 cookie 持久化，用 Streamlit 1.50+ 的 st.context.cookies (read-only) + 短暫 session
"""

from typing import Optional


def get_cookie_manager():
    """Stub: 永遠回傳 True，讓 main() 流程不會被卡住"""
    return True


def save_session_to_cookie(session_data: dict) -> None:
    """Stub: no-op"""
    pass


def load_session_from_cookie() -> Optional[dict]:
    """Stub: 永遠回 None"""
    return None


def clear_session_cookie() -> None:
    """Stub: no-op"""
    pass
