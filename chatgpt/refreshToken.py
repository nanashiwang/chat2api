import hashlib
import json
import random
import time

from fastapi import HTTPException

from utils.Client import Client
from utils.Logger import logger
from utils.configs import (
    openai_auth_client_id,
    openai_auth_redirect_uri,
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


async def chat_refresh(refresh_token):
    data = {
        "client_id": openai_auth_client_id,
        "grant_type": "refresh_token",
        "redirect_uri": openai_auth_redirect_uri,
        "refresh_token": refresh_token,
    }
    # Auth0 是 API 端点，必须走纯 API 风格调用：
    #   - impersonate=None 关闭 Safari/Chrome TLS 指纹模拟，避免被 WAF 判定为"浏览器调 API"
    #   - 显式带 Accept/Content-Type/User-Agent，让 Auth0 正确路由到 /oauth/token 而非 Universal Login 页
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ChatGPT/1.2025.084 (iOS 17.5.1; iPhone15,3; build 1402)",
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
        r = await client.post(
            "https://auth0.openai.com/oauth/token",
            json=data,
            headers=headers,
            timeout=15,
        )
        raw_text = (r.text or "").strip()
        content_type = r.headers.get("content-type", "")

        # 诊断日志：每次刷新都记录上游返回的关键元数据
        logger.info(
            f"[chat_refresh] token={token_prefix}... status={r.status_code} "
            f"ctype={content_type} body_len={len(raw_text)} proxy={'yes' if proxy_url else 'no'}"
        )

        # 200 路径：仍需防御解析
        if r.status_code == 200:
            if not raw_text:
                raise Exception("Auth0 returned empty body with status 200")
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                raise Exception(
                    f"Auth0 non-JSON response (status 200, ctype={content_type}): "
                    f"{raw_text[:200]}"
                )
            if "access_token" not in payload:
                raise Exception(
                    f"Auth0 JSON missing access_token: keys={list(payload.keys())} "
                    f"body={raw_text[:200]}"
                )
            return payload["access_token"]

        # 非 200 路径：详细分流并记录
        error_body_hint = raw_text[:300] if raw_text else "(empty body)"
        if "invalid_grant" in raw_text or "access_denied" in raw_text:
            if refresh_token not in globals.error_token_list:
                globals.error_token_list.append(refresh_token)
                persist_error_tokens()
            raise Exception(
                f"Auth0 rejected refresh_token (status {r.status_code}): {error_body_hint}. "
                f"Hint: 若 token 以 'rt_' 开头，可能需要设置 OPENAI_AUTH_CLIENT_ID 环境变量为正确的 client_id。"
            )
        raise Exception(
            f"Auth0 refresh failed (status {r.status_code}, ctype={content_type}): {error_body_hint}"
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
