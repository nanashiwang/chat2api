"""chat2api 多实例编排面板 (Orchestrator)

职责：
- 单密码登录（HMAC 签名 cookie + CSRF 双 cookie）
- 增删改账号实例（写 accounts.csv → 调 generate.py → docker compose up）
- 启停重启单实例
- 状态仪表盘（docker inspect + exit IP 抽样 + cookie age）
- 操作审计（jsonl 追加写）

所有 docker 调用走容器内 docker-cli + 挂入的 /var/run/docker.sock。
所有 compose 操作必须 --project-directory $MULTI_HOST_PATH 让 daemon 用宿主路径解析 volumes。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import secrets as pysecrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field, field_validator

# ---------- 配置 ----------

# WORK 目录策略：让容器内 WORK 路径 = 宿主 deploy/multi 绝对路径，
# 因为 docker compose 客户端（在容器内）发命令前会先解析 env_file 并检查存在性，
# 必须用容器内能访问的路径；而 daemon（在宿主）解析 volumes 用宿主路径。
# 通过 compose volume `${MULTI_HOST_PATH}:${MULTI_HOST_PATH}` 让两者重合。
HOST_PATH = (os.environ.get("MULTI_HOST_PATH") or "/work").rstrip("/")
WORK = Path(HOST_PATH)
COMPOSE_FILE_C = WORK / "generated" / "docker-compose.yml"
ACCOUNTS_CSV = WORK / "accounts.csv"
SECRETS_FILE = WORK / "generated" / "secrets.txt"
ORCH_ENV = WORK / "generated" / "orch.env"
DATA_DIR = WORK / "data"
AUDIT_FILE = WORK / "audit.jsonl"

PASSWORD = (os.environ.get("ORCH_PASSWORD") or "").strip()
SESSION_SECRET = (os.environ.get("ORCH_SESSION_SECRET") or "").strip()
SESSION_MAX_AGE = 8 * 3600  # 8h
SESSION_COOKIE = "orch_session"
CSRF_COOKIE = "orch_csrf"

if not PASSWORD or not SESSION_SECRET:
    raise RuntimeError(
        "ORCH_PASSWORD 与 ORCH_SESSION_SECRET 必须在 generated/orch.env 中设置"
    )

SLUG_RE = re.compile(r"^[a-z0-9-]{1,16}$")
PROXY_RE = re.compile(r"^(socks5|socks5h|http|https)://[^\s]+$")

LOG_LEVEL = os.environ.get("ORCH_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("orchestrator")

serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="orch-session-v1")

app = FastAPI(title="chat2api Orchestrator", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------- 工具：subprocess + docker ----------

class DockerError(Exception):
    pass


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """同步执行命令，返回 (rc, stdout, stderr)。绝不抛 stderr 给前端原文（避免泄漏路径）。"""
    logger.debug("run: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise DockerError(f"命令超时（{timeout}s）：{cmd[0]}")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def dc(*args: str, timeout: int = 180) -> tuple[int, str, str]:
    """docker compose 包装，自动加 -f 与 --project-directory。"""
    cmd = [
        "docker", "compose",
        "-f", str(COMPOSE_FILE_C),
        "--project-directory", HOST_PATH,
        *args,
    ]
    return run(cmd, timeout=timeout)


def inspect(container: str) -> dict | None:
    rc, out, _ = run(["docker", "inspect", container], timeout=10)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
        return data[0] if data else None
    except json.JSONDecodeError:
        return None


def regenerate_and_apply() -> None:
    """写 csv 后必须调用：先 generate.py，再 docker compose up -d --remove-orphans，
    再 nginx -s reload（compose 不会因 nginx.conf 改动重启 nginx）。"""
    rc, out, err = run(
        ["python3", str(WORK / "generate.py")], timeout=30
    )
    if rc != 0:
        logger.error("generate.py 失败：rc=%s out=%s err=%s", rc, out, err)
        raise DockerError(f"配置生成失败：{(err or out)[:300]}")

    rc, out, err = dc("up", "-d", "--remove-orphans", timeout=240)
    if rc != 0:
        logger.error("compose up 失败：rc=%s err=%s", rc, err)
        raise DockerError(f"docker compose 失败：{(err or out)[:300]}")

    # nginx.conf 变化必须 reload，不然新 location 不生效
    rc, out, err = run(
        ["docker", "exec", "c2a-nginx", "nginx", "-s", "reload"], timeout=10
    )
    if rc != 0:
        logger.warning("nginx reload 失败（非致命）：%s", (err or out)[:200])


# ---------- 工具：CSV / env / secrets ----------

def read_accounts() -> list[dict[str, str]]:
    if not ACCOUNTS_CSV.exists():
        return []
    out: list[dict[str, str]] = []
    with ACCOUNTS_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            slug = (row.get("slug") or "").strip()
            if not slug:
                continue
            out.append({
                "slug": slug,
                "proxy_url": (row.get("proxy_url") or "").strip(),
                "note": (row.get("note") or "").strip(),
            })
    return out


def write_accounts(rows: list[dict[str, str]]) -> None:
    """原子写：tmp + rename。csv 头固定。"""
    tmp = ACCOUNTS_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["slug", "proxy_url", "note"])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "slug": r["slug"],
                "proxy_url": r.get("proxy_url", ""),
                "note": r.get("note", ""),
            })
    tmp.replace(ACCOUNTS_CSV)


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def mask_proxy(url: str) -> str:
    """socks5://user:pass@host:port → socks5://****@host:port"""
    if not url:
        return ""
    m = re.match(r"^(\w+)://([^@/]+@)?(.+)$", url)
    if not m:
        return url
    scheme, _, host = m.groups()
    return f"{scheme}://****@{host}" if _ else f"{scheme}://{host}"


def mask_secret(s: str, head: int = 6, tail: int = 4) -> str:
    if not s:
        return ""
    if len(s) <= head + tail + 3:
        return "*" * len(s)
    return f"{s[:head]}...{s[-tail:]}"


# ---------- 出口 IP 缓存 ----------

_exit_ip_cache: dict[str, tuple[str, float]] = {}
EXIT_IP_TTL = 60.0


def get_exit_ip(slug: str, force: bool = False) -> str | None:
    now = time.time()
    if not force and slug in _exit_ip_cache:
        ip, ts = _exit_ip_cache[slug]
        if now - ts < EXIT_IP_TTL:
            return ip
    rc, out, _ = run(
        ["docker", "exec", f"c2a-{slug}", "curl", "-s", "--max-time", "6",
         "https://api.ipify.org"],
        timeout=10,
    )
    ip = out.strip() if rc == 0 and out.strip() else None
    if ip:
        _exit_ip_cache[slug] = (ip, now)
    return ip


def get_cookie_last_success(slug: str) -> int | None:
    """读 data/{slug}/refresh_map.json，返回最新 last_success_at（unix ts）。"""
    p = DATA_DIR / slug / "refresh_map.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        ts_list = [
            int(v.get("last_success_at") or v.get("timestamp") or 0)
            for v in data.values()
            if isinstance(v, dict)
        ]
        ts_list = [t for t in ts_list if t > 0]
        return max(ts_list) if ts_list else None
    except Exception:
        return None


# ---------- 审计 ----------

def audit(action: str, request: Request, ok: bool, **fields: Any) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": "admin",
        "ip": request.client.host if request.client else "?",
        "action": action,
        "ok": ok,
        **fields,
    }
    try:
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("audit write failed: %s", e)


def read_audit(limit: int = 200) -> list[dict]:
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    for line in reversed(lines[-limit * 2:]):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


# ---------- 鉴权 ----------

def issue_session_token() -> str:
    return serializer.dumps({"u": "admin"})


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return isinstance(data, dict) and data.get("u") == "admin"
    except (BadSignature, SignatureExpired):
        return False


def gen_csrf() -> str:
    return pysecrets.token_hex(16)


def require_session(
    request: Request,
    orch_session: str | None = Cookie(default=None),
) -> None:
    if not verify_session_token(orch_session):
        raise HTTPException(status_code=401, detail="未登录")


def require_csrf(request: Request) -> None:
    """双 cookie 模式：cookie orch_csrf 必须等于 header X-CSRF-Token。"""
    cookie_val = request.cookies.get(CSRF_COOKIE) or ""
    header_val = request.headers.get("x-csrf-token") or ""
    if not cookie_val or not pysecrets.compare_digest(cookie_val, header_val):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")


# 登录速率限制（同 IP 60s 内最多 5 次失败）
_login_attempts: dict[str, list[float]] = {}


def check_login_rate(ip: str) -> bool:
    now = time.time()
    bucket = _login_attempts.setdefault(ip, [])
    bucket[:] = [t for t in bucket if now - t < 60]
    return len(bucket) < 5


def record_login_failure(ip: str) -> None:
    _login_attempts.setdefault(ip, []).append(time.time())


# ---------- 路由：基础 ----------

@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None}
    )


@app.post("/login")
async def login(
    request: Request,
    response: Response,
    password: str = Form(...),
) -> Response:
    ip = request.client.host if request.client else "?"
    if not check_login_rate(ip):
        audit("login", request, False, reason="rate_limited")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "尝试过多，请稍候再试"},
            status_code=429,
        )
    if not pysecrets.compare_digest(password, PASSWORD):
        record_login_failure(ip)
        audit("login", request, False, reason="bad_password")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "密码错误"},
            status_code=401,
        )

    token = issue_session_token()
    csrf = gen_csrf()
    is_https = request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto") == "https"
    resp = RedirectResponse(url="./", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE,
        httponly=True, samesite="strict", secure=is_https, path="/",
    )
    resp.set_cookie(
        CSRF_COOKIE, csrf,
        max_age=SESSION_MAX_AGE,
        httponly=False, samesite="strict", secure=is_https, path="/",
    )
    audit("login", request, True)
    return resp


@app.post("/logout")
async def logout(request: Request, _: None = Depends(require_session)) -> Response:
    audit("logout", request, True)
    resp = RedirectResponse(url="./login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    resp.delete_cookie(CSRF_COOKIE, path="/")
    return resp


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    orch_session: str | None = Cookie(default=None),
) -> Response:
    if not verify_session_token(orch_session):
        return RedirectResponse(url="./login", status_code=303)
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ---------- API: accounts CRUD ----------

class AccountIn(BaseModel):
    slug: str
    proxy_url: str = ""
    note: str = ""

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip()
        if not SLUG_RE.match(v):
            raise ValueError("slug 不合法（需 [a-z0-9-]{1,16}）")
        return v

    @field_validator("proxy_url")
    @classmethod
    def _proxy(cls, v: str) -> str:
        v = v.strip()
        if v and not PROXY_RE.match(v):
            raise ValueError("proxy_url 必须以 socks5/socks5h/http/https:// 开头")
        return v


class AccountPatch(BaseModel):
    proxy_url: str | None = None
    note: str | None = None

    @field_validator("proxy_url")
    @classmethod
    def _proxy(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not PROXY_RE.match(v):
            raise ValueError("proxy_url 必须以 socks5/socks5h/http/https:// 开头")
        return v


@app.get("/api/accounts", dependencies=[Depends(require_session)])
async def api_list_accounts() -> JSONResponse:
    rows = read_accounts()
    out = []
    for r in rows:
        slug_env = read_env_file(WORK / "generated" / "env" / f"{r['slug']}.env")
        out.append({
            "slug": r["slug"],
            "proxy_url_masked": mask_proxy(r["proxy_url"]),
            "has_proxy": bool(r["proxy_url"]),
            "note": r["note"],
            "auth_masked": mask_secret(slug_env.get("AUTHORIZATION", "")),
            "admin_pwd_masked": mask_secret(slug_env.get("ADMIN_PASSWORD", "")),
            "api_prefix": slug_env.get("API_PREFIX", ""),
        })
    return JSONResponse({"accounts": out})


@app.post(
    "/api/accounts",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def api_add_account(payload: AccountIn, request: Request) -> JSONResponse:
    rows = read_accounts()
    if any(r["slug"] == payload.slug for r in rows):
        raise HTTPException(status_code=409, detail=f"slug={payload.slug} 已存在")
    rows.append(payload.model_dump())
    write_accounts(rows)
    try:
        regenerate_and_apply()
    except DockerError as e:
        # 回滚 csv
        write_accounts([r for r in rows if r["slug"] != payload.slug])
        audit("add_account", request, False, slug=payload.slug, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    audit("add_account", request, True, slug=payload.slug)
    return JSONResponse({"ok": True, "slug": payload.slug})


@app.patch(
    "/api/accounts/{slug}",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def api_patch_account(
    slug: str, payload: AccountPatch, request: Request
) -> JSONResponse:
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug 不合法")
    rows = read_accounts()
    target = next((r for r in rows if r["slug"] == slug), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"slug={slug} 不存在")
    if payload.proxy_url is not None:
        target["proxy_url"] = payload.proxy_url
    if payload.note is not None:
        target["note"] = payload.note
    write_accounts(rows)
    try:
        regenerate_and_apply()
    except DockerError as e:
        audit("patch_account", request, False, slug=slug, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    audit("patch_account", request, True, slug=slug, fields=payload.model_dump(exclude_none=True))
    return JSONResponse({"ok": True, "slug": slug})


@app.delete(
    "/api/accounts/{slug}",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def api_delete_account(slug: str, request: Request) -> JSONResponse:
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug 不合法")
    rows = read_accounts()
    if not any(r["slug"] == slug for r in rows):
        raise HTTPException(status_code=404, detail=f"slug={slug} 不存在")
    write_accounts([r for r in rows if r["slug"] != slug])
    try:
        regenerate_and_apply()
    except DockerError as e:
        audit("delete_account", request, False, slug=slug, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    audit("delete_account", request, True, slug=slug, note="data/ 目录保留")
    return JSONResponse({"ok": True, "slug": slug})


# ---------- API: 实例运维 ----------

ALLOWED_OPS = {"start", "stop", "restart"}


@app.post(
    "/api/instances/{slug}/{op}",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def api_instance_op(slug: str, op: str, request: Request) -> JSONResponse:
    if op not in ALLOWED_OPS:
        raise HTTPException(status_code=400, detail=f"非法操作 {op}")
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug 不合法")
    if not any(r["slug"] == slug for r in read_accounts()):
        raise HTTPException(status_code=404, detail=f"slug={slug} 不存在")
    rc, out, err = dc(op, f"chat2api-{slug}", timeout=120)
    success = rc == 0
    audit(f"instance_{op}", request, success, slug=slug,
          err=(err or "")[:200] if not success else "")
    if not success:
        raise HTTPException(status_code=500, detail=(err or out)[:300])
    return JSONResponse({"ok": True, "slug": slug, "op": op})


# ---------- API: 状态 ----------

@app.get("/api/status", dependencies=[Depends(require_session)])
async def api_status() -> JSONResponse:
    rows = read_accounts()
    instances = []
    for r in rows:
        slug = r["slug"]
        info = inspect(f"c2a-{slug}") or {}
        state = info.get("State", {})
        health = state.get("Health", {}).get("Status") if state else None
        started_at = state.get("StartedAt") if state else None
        uptime_seconds: int | None = None
        if started_at:
            try:
                # docker 的 StartedAt 是 RFC3339 + 纳秒；切到 26 位再 fromisoformat
                ts_str = started_at.replace("Z", "+00:00")[:26] + "+00:00" \
                    if "." in started_at and "Z" in started_at else started_at.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts_str)
                uptime_seconds = int(
                    (datetime.now(timezone.utc) - dt).total_seconds()
                )
            except (ValueError, TypeError):
                uptime_seconds = None
        instances.append({
            "slug": slug,
            "container": f"c2a-{slug}",
            "state": state.get("Status") if state else "absent",
            "health": health or "n/a",
            "started_at": started_at,
            "uptime_seconds": uptime_seconds,
            "proxy_masked": mask_proxy(r["proxy_url"]),
            "has_proxy": bool(r["proxy_url"]),
            "exit_ip": _exit_ip_cache.get(slug, (None, 0))[0],
            "cookie_last_success_at": get_cookie_last_success(slug),
            "note": r["note"],
        })
    return JSONResponse({
        "instances": instances,
        "server_time": int(time.time()),
    })


@app.get(
    "/api/instances/{slug}/exit-ip",
    dependencies=[Depends(require_session)],
)
async def api_exit_ip(slug: str, force: int = Query(0)) -> JSONResponse:
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug 不合法")
    ip = get_exit_ip(slug, force=bool(force))
    return JSONResponse({"slug": slug, "exit_ip": ip or ""})


# ---------- API: 凭证查看（敏感） ----------

@app.get(
    "/api/secrets/{slug}",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
async def api_reveal_secret(slug: str, request: Request) -> JSONResponse:
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="slug 不合法")
    env = read_env_file(WORK / "generated" / "env" / f"{slug}.env")
    if not env:
        audit("reveal_secret", request, False, slug=slug, reason="not_found")
        raise HTTPException(status_code=404, detail=f"slug={slug} 凭证不存在")
    audit("reveal_secret", request, True, slug=slug)
    return JSONResponse({
        "slug": slug,
        "AUTHORIZATION": env.get("AUTHORIZATION", ""),
        "ADMIN_PASSWORD": env.get("ADMIN_PASSWORD", ""),
        "API_PREFIX": env.get("API_PREFIX", ""),
        "PROXY_URL": env.get("PROXY_URL", ""),
    })


# ---------- API: 审计 ----------

@app.get("/api/audit", dependencies=[Depends(require_session)])
async def api_audit(limit: int = Query(200, ge=1, le=2000)) -> JSONResponse:
    return JSONResponse({"records": read_audit(limit)})
