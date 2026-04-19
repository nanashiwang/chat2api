import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import app, templates
from utils.configs import admin_password, api_prefix, authorization_list
from utils.routing import (
    build_group_assignments,
    get_dashboard_payload,
    get_routing_config,
    save_routing_config,
    sync_bindings_to_fp,
    update_single_binding,
)
import utils.globals as globals

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
    for token in incoming_tokens:
        if token not in existing:
            globals.token_list.append(token)
            existing.add(token)
            added.append(token)

    if added:
        with open(globals.TOKENS_FILE, "a", encoding="utf-8") as f:
            for token in added:
                f.write(token + "\n")

    return JSONResponse(
        {
            "status": "success",
            "added_count": len(added),
            "skipped_count": len(incoming_tokens) - len(added),
            "message": "账号已导入；如需固定分组，请重新发布绑定规则。",
        }
    )


app.add_api_route("/admin/routing", routing_admin_page, methods=["GET"], response_class=HTMLResponse)
app.add_api_route("/admin/routing/data", routing_admin_data, methods=["GET"])
app.add_api_route("/admin/routing/save", routing_admin_save, methods=["POST"])
app.add_api_route("/admin/login", routing_admin_login_page, methods=["GET"], response_class=HTMLResponse)
app.add_api_route("/admin/login", routing_admin_login_submit, methods=["POST"])
app.add_api_route("/admin/logout", routing_admin_logout, methods=["POST"])
app.add_api_route("/admin/routing/account-bind", routing_admin_bind_account, methods=["POST"])
app.add_api_route("/admin/routing/accounts/import", routing_admin_import_accounts, methods=["POST"])

if api_prefix:
    app.add_api_route(f"/{api_prefix}/admin/routing", routing_admin_page, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route(f"/{api_prefix}/admin/routing/data", routing_admin_data, methods=["GET"])
    app.add_api_route(f"/{api_prefix}/admin/routing/save", routing_admin_save, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/login", routing_admin_login_page, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route(f"/{api_prefix}/admin/login", routing_admin_login_submit, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/logout", routing_admin_logout, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/account-bind", routing_admin_bind_account, methods=["POST"])
    app.add_api_route(f"/{api_prefix}/admin/routing/accounts/import", routing_admin_import_accounts, methods=["POST"])
