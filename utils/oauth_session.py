"""OAuth PKCE session 管理（纯内存 + TTL）。

用于 Harvester 的"浏览器登录"流程：
  1. start(email, ...) → 生成 PKCE + state + session_id，存入内存，返回 authorize_url
  2. 用户在真实浏览器完成 OAuth，拿到 com.openai.chat://...?code=X&state=Y 回调
  3. exchange(session_id, callback_url) → 校验 state + 用 verifier 换 token

设计：
  - 内存 dict，不持久化（服务重启即失效，符合用户选择）
  - 15 分钟 TTL（Auth0 的 code 也是 ~10 分钟过期）
  - 线程安全（threading.Lock）
  - 自动清理过期会话（每次写操作时机会性清理）
"""

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urlencode


# 与 chatgpt/refreshToken.py 和 gateway/share.py 一致的 iOS client_id
# 可通过环境变量覆盖（同 chat_refresh）
_DEFAULT_CLIENT_ID = "pdlLIX2Y72MIl2rhLhTE9VV9bN905kBh"
_DEFAULT_REDIRECT_URI = "com.openai.chat://auth0.openai.com/ios/com.openai.chat/callback"
_DEFAULT_AUDIENCE = "https://api.openai.com/v1"
_DEFAULT_SCOPE = (
    "openid email profile offline_access model.request model.read "
    "organization.read organization.write"
)

AUTH_BASE = "https://auth0.openai.com/authorize"
TOKEN_ENDPOINT = "https://auth0.openai.com/oauth/token"

SESSION_TTL_SECONDS = 15 * 60


@dataclass
class OAuthSession:
    session_id: str
    verifier: str
    challenge: str
    state: str
    email: str
    note: str = ""
    proxy_name: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))

    def expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL_SECONDS


# ========================= 内存存储 =========================

_sessions: Dict[str, OAuthSession] = {}
_lock = threading.Lock()


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _gen_pkce_pair() -> tuple:
    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _gen_state() -> str:
    return _base64url(secrets.token_bytes(16))


def _gen_session_id() -> str:
    return _base64url(secrets.token_bytes(12))


def _gc_expired() -> None:
    """机会性清理过期会话，调用方需已持锁。"""
    now = time.time()
    expired = [
        sid for sid, s in _sessions.items()
        if now - s.created_at > SESSION_TTL_SECONDS
    ]
    for sid in expired:
        _sessions.pop(sid, None)


# ========================= 对外 API =========================

def _get_oauth_config():
    """允许通过 configs 覆盖 client_id / redirect_uri 等。"""
    try:
        from utils import configs
        client_id = getattr(configs, "openai_auth_client_id", None) or _DEFAULT_CLIENT_ID
        redirect_uri = getattr(configs, "openai_auth_redirect_uri", None) or _DEFAULT_REDIRECT_URI
        audience = getattr(configs, "openai_auth_audience", None) or _DEFAULT_AUDIENCE
        scope = getattr(configs, "openai_auth_scope", None) or _DEFAULT_SCOPE
    except Exception:
        client_id = _DEFAULT_CLIENT_ID
        redirect_uri = _DEFAULT_REDIRECT_URI
        audience = _DEFAULT_AUDIENCE
        scope = _DEFAULT_SCOPE
    return client_id, redirect_uri, audience, scope


def start_session(email: str, note: str = "", proxy_name: str = "") -> Dict:
    """生成一次性 OAuth 授权会话，返回前端需要的 URL + session_id。"""
    if not email or "@" not in email:
        raise ValueError("email 不合法")

    client_id, redirect_uri, audience, scope = _get_oauth_config()

    verifier, challenge = _gen_pkce_pair()
    state = _gen_state()
    session_id = _gen_session_id()

    sess = OAuthSession(
        session_id=session_id,
        verifier=verifier,
        challenge=challenge,
        state=state,
        email=email.strip(),
        note=(note or "").strip(),
        proxy_name=(proxy_name or "").strip(),
    )
    with _lock:
        _gc_expired()
        _sessions[session_id] = sess

    params = {
        "client_id": client_id,
        "audience": audience,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "prompt": "login",
    }
    authorize_url = f"{AUTH_BASE}?{urlencode(params)}"
    return {
        "session_id": session_id,
        "authorize_url": authorize_url,
        "redirect_uri": redirect_uri,
        "expires_in": SESSION_TTL_SECONDS,
    }


def pop_session(session_id: str) -> Optional[OAuthSession]:
    """取出并删除会话（一次性使用）。过期返回 None。"""
    with _lock:
        sess = _sessions.pop(session_id, None)
    if not sess:
        return None
    if sess.expired():
        return None
    return sess


def peek_session(session_id: str) -> Optional[OAuthSession]:
    """不删除地查看会话（供调试）。"""
    with _lock:
        sess = _sessions.get(session_id)
    if sess and sess.expired():
        return None
    return sess


def stats() -> Dict:
    with _lock:
        _gc_expired()
        total = len(_sessions)
    return {"active_sessions": total, "ttl_seconds": SESSION_TTL_SECONDS}
