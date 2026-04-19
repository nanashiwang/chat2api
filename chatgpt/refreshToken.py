import hashlib
import json
import random
import time

from fastapi import HTTPException

from utils.Client import Client
from utils.Logger import logger
from utils.configs import proxy_url_list
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
        "client_id": "pdlLIX2Y72MIl2rhLhTE9VV9bN905kBh",
        "grant_type": "refresh_token",
        "redirect_uri": "com.openai.chat://auth0.openai.com/ios/com.openai.chat/callback",
        "refresh_token": refresh_token
    }
    session_id = hashlib.md5(refresh_token.encode()).hexdigest()
    bound_proxy = get_bound_proxy(refresh_token)
    proxy_url = bound_proxy or (random.choice(proxy_url_list).replace("{}", session_id) if proxy_url_list else None)
    if proxy_url:
        proxy_url = proxy_url.replace("{}", session_id)
    refresh_meta = globals.refresh_map.get(refresh_token, {})
    refresh_meta["last_proxy"] = proxy_url or ""
    globals.refresh_map[refresh_token] = refresh_meta
    client = Client(proxy=proxy_url)
    try:
        r = await client.post("https://auth0.openai.com/oauth/token", json=data, timeout=15)
        if r.status_code == 200:
            access_token = r.json()['access_token']
            return access_token
        else:
            if "invalid_grant" in r.text or "access_denied" in r.text:
                if refresh_token not in globals.error_token_list:
                    globals.error_token_list.append(refresh_token)
                    persist_error_tokens()
                raise Exception(r.text)
            else:
                raise Exception(r.text[:300])
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
        logger.error(f"Failed to refresh access_token `{refresh_token}`: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to refresh access_token.")
    finally:
        await client.close()
        del client
