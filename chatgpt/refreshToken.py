import hashlib
import json
import random
import time
from urllib.parse import urlencode

from fastapi import HTTPException

from utils.Client import Client
from utils.Logger import logger
from utils.configs import (
    openai_auth_client_id,
    openai_auth_redirect_uri,
    openai_auth_scope,
    openai_auth_token_url,
    proxy_url_list,
)
from utils.routing import get_bound_proxy
import utils.globals as globals


def persist_refresh_map():
    with open(globals.REFRESH_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(globals.refresh_map, f, indent=4, ensure_ascii=False)


def persist_error_tokens():
    with open(globals.ERROR_TOKENS_FILE, "w", encoding="utf-8") as f:
        for token in globals.error_token_list:
            f.write(token + "\n")


async def rt2ac(refresh_token, force_refresh=False):
    if not force_refresh and (refresh_token in globals.refresh_map and int(time.time()) - globals.refresh_map.get(refresh_token, {}).get("timestamp", 0) < 5 * 24 * 60 * 60):
        access_token = globals.refresh_map[refresh_token]["token"]
        # logger.info(f"refresh_token -> access_token from cache")
        return access_token
    else:
        try:
            access_token = await chat_refresh(refresh_token)
            refresh_meta = globals.refresh_map.get(refresh_token, {})
            now = int(time.time())
            refresh_meta.update({
                "token": access_token,
                "timestamp": now,
                "last_success_at": now,
                "last_error": "",
                "last_error_at": 0,
                "fail_count": 0,
            })
            globals.refresh_map[refresh_token] = refresh_meta
            if refresh_token in globals.error_token_list:
                globals.error_token_list[:] = [item for item in globals.error_token_list if item != refresh_token]
                persist_error_tokens()
            persist_refresh_map()
            logger.info(f"refresh_token -> access_token with openai: {access_token}")
            return access_token
        except HTTPException as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)


async def sess2ac(session_token, force_refresh=False):
    """Session cookie → access_token。

    `session_token` 是带 'sess-' 前缀的存储形态（外部传入时已剥除 or 保留都支持）。
    缓存 8 分钟（session accessToken 寿命约 10-15 分钟）。
    """
    # 统一 key：带前缀的是存储形态，剥除后的是 cookie 真实值
    if session_token.startswith("sess-"):
        storage_key = session_token
        cookie_value = session_token[5:]
    else:
        storage_key = "sess-" + session_token
        cookie_value = session_token

    # 缓存命中
    if (not force_refresh
            and storage_key in globals.refresh_map
            and int(time.time()) - globals.refresh_map.get(storage_key, {}).get("timestamp", 0) < 8 * 60):
        cached = globals.refresh_map[storage_key].get("token")
        if cached:
            return cached

    try:
        access_token = await fetch_session_access_token(cookie_value)
        refresh_meta = globals.refresh_map.get(storage_key, {})
        now = int(time.time())
        refresh_meta.update({
            "token": access_token,
            "timestamp": now,
            "last_success_at": now,
            "last_error": "",
            "last_error_at": 0,
            "fail_count": 0,
        })
        globals.refresh_map[storage_key] = refresh_meta
        if storage_key in globals.error_token_list:
            globals.error_token_list[:] = [item for item in globals.error_token_list if item != storage_key]
            persist_error_tokens()
        persist_refresh_map()
        logger.info(f"session_cookie -> access_token OK (key={storage_key[:12]}...)")
        return access_token
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


async def fetch_session_access_token(session_cookie):
    """带 __Secure-next-auth.session-token cookie 访问 chatgpt.com/api/auth/session。

    返回响应 JSON 中的 accessToken 字段（JWT，调 chatgpt.com/backend-api 的 Bearer）。
    """
    session_id = hashlib.md5(session_cookie.encode()).hexdigest()
    storage_key = "sess-" + session_cookie
    bound_proxy = get_bound_proxy(storage_key)
    proxy_url = bound_proxy or (random.choice(proxy_url_list).replace("{}", session_id) if proxy_url_list else None)
    if proxy_url:
        proxy_url = proxy_url.replace("{}", session_id)
    refresh_meta = globals.refresh_map.get(storage_key, {})
    refresh_meta["last_proxy"] = proxy_url or ""
    globals.refresh_map[storage_key] = refresh_meta

    # 注意：chatgpt.com/api/auth/session 是 NextAuth 端点，需要浏览器风格 UA + Accept
    client = Client(proxy=proxy_url, impersonate="chrome124")
    cookie_key = "__Secure-next-auth.session-token"
    try:
        r = await client.get(
            "https://chatgpt.com/api/auth/session",
            headers={
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Cookie": f"{cookie_key}={session_cookie}",
            },
            timeout=15,
        )
        raw_text = (r.text or "").strip()
        content_type = r.headers.get("content-type", "")
        logger.info(
            f"[sess2ac] key={storage_key[:12]}... status={r.status_code} "
            f"ctype={content_type} body_len={len(raw_text)} proxy={'yes' if proxy_url else 'no'}"
        )

        if r.status_code != 200:
            if storage_key not in globals.error_token_list and r.status_code in (401, 403):
                globals.error_token_list.append(storage_key)
                persist_error_tokens()
            raise Exception(
                f"chatgpt.com/api/auth/session status={r.status_code}: {raw_text[:200]}"
            )
        if not raw_text:
            raise Exception("chatgpt.com/api/auth/session 返回空响应；cookie 可能已失效")

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            raise Exception(f"非 JSON 响应 ctype={content_type}: {raw_text[:200]}")

        # 未登录时 NextAuth 返回 {} 或 {"user": null}
        access_token = payload.get("accessToken") or payload.get("access_token")
        if not access_token:
            if storage_key not in globals.error_token_list:
                globals.error_token_list.append(storage_key)
                persist_error_tokens()
            raise Exception(
                f"session cookie 无效或过期（response keys={list(payload.keys())}）"
            )
        return access_token
    except Exception as e:
        now = int(time.time())
        refresh_meta = globals.refresh_map.get(storage_key, {})
        refresh_meta.update({
            "last_error": str(e)[:300],
            "last_error_at": now,
            "fail_count": int(refresh_meta.get("fail_count", 0)) + 1,
            "last_proxy": proxy_url or "",
        })
        globals.refresh_map[storage_key] = refresh_meta
        persist_refresh_map()
        logger.error(f"[sess2ac] key={storage_key[:12]}... failed: {str(e)[:400]}")
        raise HTTPException(status_code=500, detail=str(e)[:300])
    finally:
        await client.close()
        del client


async def chat_refresh(refresh_token):
    # 使用 Codex CLI 风格：application/x-www-form-urlencoded + auth.openai.com
    # 老版 auth0.openai.com + iOS client_id 已返回 404
    form_body = urlencode({
        "grant_type": "refresh_token",
        "client_id": openai_auth_client_id,
        "refresh_token": refresh_token,
        "scope": openai_auth_scope,
    })
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Codex_CLI/0.1.0",
    }
    session_id = hashlib.md5(refresh_token.encode()).hexdigest()
    bound_proxy = get_bound_proxy(refresh_token)
    proxy_url = bound_proxy or (random.choice(proxy_url_list).replace("{}", session_id) if proxy_url_list else None)
    if proxy_url:
        proxy_url = proxy_url.replace("{}", session_id)
    refresh_meta = globals.refresh_map.get(refresh_token, {})
    refresh_meta["last_proxy"] = proxy_url or ""
    globals.refresh_map[refresh_token] = refresh_meta
    client = Client(proxy=proxy_url, impersonate=None)
    token_prefix = refresh_token[:8]
    try:
        r = await client.post(openai_auth_token_url, data=form_body, headers=headers, timeout=15)
        raw_text = (r.text or "").strip()
        content_type = r.headers.get("content-type", "")

        # 诊断日志：每次刷新都记录上游返回的关键元数据
        logger.info(
            f"[chat_refresh] token={token_prefix}... status={r.status_code} "
            f"ctype={content_type} body_len={len(raw_text)} proxy={'yes' if proxy_url else 'no'} "
            f"endpoint={openai_auth_token_url}"
        )

        # 200 路径：仍需防御解析
        if r.status_code == 200:
            if not raw_text:
                raise Exception("OpenAI returned empty body with status 200")
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                raise Exception(
                    f"OpenAI non-JSON response (status 200, ctype={content_type}): "
                    f"{raw_text[:200]}"
                )
            if "access_token" not in payload:
                raise Exception(
                    f"OpenAI JSON missing access_token: keys={list(payload.keys())} "
                    f"body={raw_text[:200]}"
                )
            return payload["access_token"]

        # 非 200 路径：详细分流并记录
        error_body_hint = raw_text[:300] if raw_text else "(empty body)"
        if "invalid_grant" in raw_text or "access_denied" in raw_text or "refresh_token_expired" in raw_text:
            if refresh_token not in globals.error_token_list:
                globals.error_token_list.append(refresh_token)
                persist_error_tokens()
            raise Exception(
                f"OpenAI rejected refresh_token (status {r.status_code}): {error_body_hint}"
            )
        raise Exception(
            f"OpenAI refresh failed (status {r.status_code}, ctype={content_type}): {error_body_hint}"
        )
    except Exception as e:
        now = int(time.time())
        refresh_meta = globals.refresh_map.get(refresh_token, {})
        refresh_meta.update({
            "last_error": str(e)[:300],
            "last_error_at": now,
            "fail_count": int(refresh_meta.get("fail_count", 0)) + 1,
            "last_proxy": proxy_url or "",
        })
        globals.refresh_map[refresh_token] = refresh_meta
        persist_refresh_map()
        logger.error(f"[chat_refresh] token={token_prefix}... failed: {str(e)[:400]}")
        raise HTTPException(status_code=500, detail=str(e)[:300])
    finally:
        await client.close()
        del client
