import json
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import app, templates
from utils.Client import Client
from utils.configs import admin_password, api_prefix, authorization_list
from utils.routing import (
    build_group_assignments,
    detect_token_type,
    get_dashboard_payload,
    get_routing_config,
    remove_account_binding,
    save_routing_config,
    sync_bindings_to_fp,
    update_account_meta,
    update_single_binding,
)
import utils.globals as globals
from chatgpt.refreshToken import rt2ac

ADMIN_COOKIE_NAME = "admin_auth"
ADMIN_COOKIE_MAX_AGE = 8 * 60 * 60
rate_limit_buckets = defaultdict(deque)
failed_login_buckets = defaultdict(deque)


def admin_login_path():
    if api_prefix:
        return f"/{api_prefix}/admin/login"
    return "/admin/login"


def get_admin_secrets():
    if admin_password:
        return [admin_password]
    return authorization_list


def get_client_key(request: Request):
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client:
        return request.client.host
    return "unknown"


def check_rate_limit(bucket_key, limit, window_seconds):
    now = time.time()
    bucket = rate_limit_buckets[bucket_key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="Too many admin requests")
    bucket.append(now)


def record_failed_login(client_key):
    now = time.time()
    bucket = failed_login_buckets[client_key]
    while bucket and now - bucket[0] > 600:
        bucket.popleft()
    bucket.append(now)


def ensure_login_not_locked(client_key):
    now = time.time()
    bucket = failed_login_buckets[client_key]
    while bucket and now - bucket[0] > 600:
        bucket.popleft()
    if len(bucket) >= 5:
        raise HTTPException(status_code=429, detail="Too many failed login attempts")


def is_admin_authorized(request: Request):
    cookie_token = request.cookies.get(ADMIN_COOKIE_NAME, "")
    header_token = request.headers.get("authorization", "").replace("Bearer ", "").strip()
    token = header_token or cookie_token
    return bool(token and token in get_admin_secrets())


def get_current_admin_token(request: Request):
    cookie_token = request.cookies.get(ADMIN_COOKIE_NAME, "")
    header_token = request.headers.get("authorization", "").replace("Bearer ", "").strip()
    token = header_token or cookie_token
    if token and token in get_admin_secrets():
        return token
    return ""


def require_admin_auth(request: Request):
    if not get_admin_secrets():
        return
    check_rate_limit(f"admin:{get_client_key(request)}", 120, 60)
    if not is_admin_authorized(request):
        raise HTTPException(status_code=401, detail="Admin authorization required")


async def routing_admin_login_page(request: Request):
    check_rate_limit(f"admin-page:{get_client_key(request)}", 60, 60)
    if is_admin_authorized(request):
        return RedirectResponse(url=f"/{api_prefix}/admin/routing" if api_prefix else "/admin/routing", status_code=302)
    return templates.TemplateResponse(
        "admin_login.html",
        {
            "request": request,
            "api_prefix": api_prefix,
        },
    )


async def routing_admin_login_submit(request: Request):
    client_key = get_client_key(request)
    ensure_login_not_locked(client_key)
    check_rate_limit(f"admin-login:{client_key}", 10, 300)
    form = await request.form()
    password = (form.get("password") or "").strip()
    if not password or password not in get_admin_secrets():
        record_failed_login(client_key)
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "api_prefix": api_prefix,
                "error": "授权码无效",
            },
            status_code=401,
        )

    response = RedirectResponse(
        url=f"/{api_prefix}/admin/routing" if api_prefix else "/admin/routing",
        status_code=302,
    )
    failed_login_buckets.pop(client_key, None)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        value=password,
        httponly=True,
        samesite="lax",
        max_age=ADMIN_COOKIE_MAX_AGE,
    )
    return response


async def routing_admin_logout(request: Request):
    response = RedirectResponse(url=admin_login_path(), status_code=302)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


async def routing_admin_page(request: Request):
    check_rate_limit(f"admin-page:{get_client_key(request)}", 60, 60)
    if get_admin_secrets() and not is_admin_authorized(request):
        return RedirectResponse(url=admin_login_path(), status_code=302)
    return templates.TemplateResponse(
        "account_proxy_bindings.html",
        {
            "request": request,
            "api_prefix": api_prefix,
            "admin_token": get_current_admin_token(request),
        },
    )


async def routing_admin_data(request: Request):
    require_admin_auth(request)
    payload = get_dashboard_payload()
    payload["routing_config"] = get_routing_config()
    payload["proxy_options"] = get_routing_config().get("proxies", [])
    return JSONResponse(payload)


async def routing_admin_save(request: Request):
    require_admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    proxies = body.get("proxies", [])
    group_size = body.get("group_size", 25)
    if not isinstance(proxies, list) or not proxies:
        raise HTTPException(status_code=400, detail="proxies is required")

    result = build_group_assignments(list(globals.token_list), proxies, group_size)
    save_routing_config(result)
    sync_bindings_to_fp(result["bindings"])
    return JSONResponse(
        {
            "status": "success",
            "message": "Routing config saved",
            "summary": get_dashboard_payload()["summary"],
        }
    )


async def routing_admin_bind_account(request: Request):
    require_admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    token = (body.get("token") or "").strip()
    proxy_url = (body.get("proxy_url") or "").strip()
    proxy_name = (body.get("proxy_name") or "").strip()
    if not token or not proxy_url:
        raise HTTPException(status_code=400, detail="token and proxy_url are required")

    config = get_routing_config()
    if not proxy_name:
        proxy = next((item for item in config.get("proxies", []) if item.get("proxy_url") == proxy_url), None)
        proxy_name = proxy.get("name") if proxy else "Custom Proxy"

    binding = update_single_binding(token, proxy_name, proxy_url)
    return JSONResponse({"status": "success", "binding": binding})


async def routing_admin_import_accounts(request: Request):
    require_admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    text = (body.get("text") or "").strip()
    note = (body.get("note") or "").strip()
    group_name = (body.get("group_name") or "").strip()
    proxy_url = (body.get("proxy_url") or "").strip()
    proxy_name = (body.get("proxy_name") or "").strip()
    overwrite_existing = bool(body.get("overwrite_existing"))
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    incoming_tokens = []
    for line in text.splitlines():
        token = line.strip()
        if token and not token.startswith("#"):
            incoming_tokens.append(token)

    if not incoming_tokens:
        raise HTTPException(status_code=400, detail="No valid tokens found")

    existing = set(globals.token_list)
    added = []
    updated = []
    for token in incoming_tokens:
        if token not in existing:
            globals.token_list.append(token)
            existing.add(token)
            added.append(token)
        elif overwrite_existing:
            updated.append(token)

    if added:
        with open(globals.TOKENS_FILE, "a", encoding="utf-8") as f:
            for token in added:
                f.write(token + "\n")

    config = get_routing_config()
    if proxy_url and not proxy_name:
        proxy = next((item for item in config.get("proxies", []) if item.get("proxy_url") == proxy_url), None)
        proxy_name = proxy.get("name") if proxy else "Custom Proxy"

    for token in added + updated:
        update_account_meta(
            token,
            note=note,
            group_name=group_name or None,
            proxy_name=proxy_name or None,
            proxy_url=proxy_url or None,
        )

    return JSONResponse(
        {
            "status": "success",
            "added_count": len(added),
            "updated_count": len(updated),
            "skipped_count": len(incoming_tokens) - len(added) - len(updated),
            "message": "账号已保存",
        }
    )


async def routing_admin_delete_account(request: Request):
    require_admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")

    if token not in globals.token_list:
        raise HTTPException(status_code=404, detail="token not found")

    globals.token_list[:] = [item for item in globals.token_list if item != token]
    with open(globals.TOKENS_FILE, "w", encoding="utf-8") as f:
        for item in globals.token_list:
            f.write(item + "\n")

    remove_account_binding(token)
    if token in globals.refresh_map:
        globals.refresh_map.pop(token, None)
        with open(globals.REFRESH_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(globals.refresh_map, f, indent=4, ensure_ascii=False)
    if token in globals.error_token_list:
        globals.error_token_list[:] = [item for item in globals.error_token_list if item != token]
        with open(globals.ERROR_TOKENS_FILE, "w", encoding="utf-8") as f:
            for item in globals.error_token_list:
                f.write(item + "\n")

    return JSONResponse(
        {
            "status": "success",
            "message": "账号已删除",
        }
    )


async def routing_admin_refresh_account(request: Request):
    require_admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token is required")
    if token not in globals.token_list:
        raise HTTPException(status_code=404, detail="token not found")
    if detect_token_type(token) != "RefreshToken":
        raise HTTPException(status_code=400, detail="Only RefreshToken supports manual refresh")

    try:
        access_token = await rt2ac(token, force_refresh=True)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    refresh_info = globals.refresh_map.get(token, {})
    return JSONResponse(
        {
            "status": "success",
            "message": "RefreshToken 刷新成功",
            "token_masked": f"{access_token[:6]}...{access_token[-4:]}" if access_token else "",
            "refresh_updated_at": refresh_info.get("last_success_at", refresh_info.get("timestamp", 0)),
        }
    )


async def routing_admin_refresh_all_accounts(request: Request):
    require_admin_auth(request)
    refresh_tokens = [token for token in globals.token_list if detect_token_type(token) == "RefreshToken"]
    if not refresh_tokens:
        return JSONResponse(
            {
                "status": "success",
                "message": "当前没有可刷新的 RefreshToken",
                "success_count": 0,
                "failed_count": 0,
            }
        )

    success_count = 0
    failed_tokens = []
    for token in refresh_tokens:
        try:
            await rt2ac(token, force_refresh=True)
            success_count += 1
        except HTTPException:
            failed_tokens.append(token)

    message = f"批量刷新完成：成功 {success_count}，失败 {len(failed_tokens)}"
    return JSONResponse(
        {
            "status": "success" if not failed_tokens else "partial",
            "message": message,
            "success_count": success_count,
            "failed_count": len(failed_tokens),
            "failed_tokens": failed_tokens,
        }
    )


async def routing_admin_test_proxy(request: Request):
    require_admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    proxy_url = (body.get("proxy_url") or "").strip()
    if not proxy_url:
        raise HTTPException(status_code=400, detail="proxy_url is required")

    client = Client(proxy=proxy_url, timeout=15)
    results = []
    try:
        targets = [
            "https://chatgpt.com",
            "https://auth0.openai.com",
        ]
        for target in targets:
            try:
                response = await client.get(target, timeout=10)
                results.append({
                    "target": target,
                    "ok": 200 <= response.status_code < 500,
                    "status_code": response.status_code,
                })
            except Exception as exc:
                results.append({
                    "target": target,
                    "ok": False,
                    "error": str(exc),
                })

        overall_ok = all(item.get("ok") for item in results)
        return JSONResponse(
            {
                "status": "success" if overall_ok else "partial",
                "proxy_url": proxy_url,
                "results": results,
            }
        )
    finally:
        await client.close()


app.add_api_route("/admin/routing", routing_admin_page, methods=["GET"], response_class=HTMLResponse)
app.add_api_route("/admin/routing/data", routing_admin_data, methods=["GET"])
app.add_api_route("/admin/routing/save", routing_admin_save, methods=["POST"])
app.add_api_route("/admin/login", routing_admin_login_page, methods=["GET"], response_class=HTMLResponse)
app.add_api_route("/admin/login", routing_admin_login_submit, methods=["POST"])
app.add_api_route("/admin/logout", routing_admin_logout, methods=["POST"])
app.add_api_route("/admin/routing/account-bind", routing_admin_bind_account, methods=["POST"])
app.add_api_route("/admin/routing/accounts/import", routing_admin_import_accounts, methods=["POST"])
app.add_api_route("/admin/routing/accounts/delete", routing_admin_delete_account, methods=["POST"])
app.add_api_route("/admin/routing/accounts/refresh", routing_admin_refresh_account, methods=["POST"])
app.add_api_route("/admin/routing/accounts/refresh-all", routing_admin_refresh_all_accounts, methods=["POST"])
app.add_api_route("/admin/routing/test-proxy", routing_admin_test_proxy, methods=["POST"])

if api_prefix:
    app.add_api_route(f"/{api_prefix}/admin/routing", routing_admin_page, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route(f"/{api_prefix}/admin/routing/data", routing_admin_data, methods=["GET"])
    app.add_api_route(f"/{api_prefix}/admin/routing/save", routing_admin_save, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/login", routing_admin_login_page, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route(f"/{api_prefix}/admin/login", routing_admin_login_submit, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/logout", routing_admin_logout, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/account-bind", routing_admin_bind_account, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/accounts/import", routing_admin_import_accounts, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/accounts/delete", routing_admin_delete_account, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/accounts/refresh", routing_admin_refresh_account, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/accounts/refresh-all", routing_admin_refresh_all_accounts, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/test-proxy", routing_admin_test_proxy, methods=["POST"])
