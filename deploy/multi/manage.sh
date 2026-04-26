#!/usr/bin/env bash
# chat2api 多实例运维包装（KISS）
# 全部子命令幂等：底层都是 generate.py + docker compose
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

C_RESET="\033[0m"; C_INFO="\033[1;34m"; C_OK="\033[1;32m"; C_ERR="\033[1;31m"
log()  { echo -e "${C_INFO}[*]${C_RESET} $*"; }
ok()   { echo -e "${C_OK}[\u2713]${C_RESET} $*"; }
err()  { echo -e "${C_ERR}[\u2717]${C_RESET} $*" >&2; }

CSV="$DIR/accounts.csv"
EXAMPLE_CSV="$DIR/accounts.example.csv"
GEN_DIR="$DIR/generated"
COMPOSE="$GEN_DIR/docker-compose.yml"

# orchestrator 必需：让容器内的 docker compose --project-directory 指向宿主路径
export MULTI_HOST_PATH="$DIR"

ensure_csv() {
    if [ ! -f "$CSV" ]; then
        if [ -f "$EXAMPLE_CSV" ]; then
            cp "$EXAMPLE_CSV" "$CSV"
            log "已复制 accounts.example.csv → accounts.csv，请编辑后再次运行"
            exit 0
        fi
        err "accounts.csv 不存在且无 example 可复制"
        exit 1
    fi
}

require_compose() {
    if [ ! -f "$COMPOSE" ]; then
        err "尚未生成，请先 ./manage.sh init"
        exit 1
    fi
}

dc() {
    docker compose -f "$COMPOSE" --project-directory "$DIR" "$@"
}

cmd_apply() {
    ensure_csv
    log "生成配置..."
    python3 "$DIR/generate.py"
    if dc config --services 2>/dev/null | grep -qx orchestrator; then
        log "构建 orchestrator 镜像..."
        dc build orchestrator
    fi
    log "应用 docker compose..."
    dc up -d --remove-orphans
    # nginx.conf 变化时 compose 不会重启 nginx，主动 reload
    if docker ps --format '{{.Names}}' | grep -qx c2a-nginx; then
        docker exec c2a-nginx nginx -s reload 2>/dev/null \
            && log "nginx reload OK" \
            || log "nginx reload 失败（首次启动可忽略）"
    fi
    ok "完成。运行 ./manage.sh secrets 查看凭证 / 编排面板访问入口"
}

cmd_init() {
    cmd_apply
}

cmd_add() {
    local slug="${1:-}" proxy="${2:-}" note="${3:-}"
    if [ -z "$slug" ]; then
        err "用法: ./manage.sh add <slug> [proxy_url] [note]"
        exit 1
    fi
    ensure_csv
    if grep -q "^${slug}," "$CSV" 2>/dev/null; then
        err "slug='$slug' 已存在于 accounts.csv"
        exit 1
    fi
    echo "${slug},${proxy},${note}" >> "$CSV"
    ok "已追加到 accounts.csv: $slug"
    cmd_apply
}

cmd_remove() {
    local slug="${1:-}"
    if [ -z "$slug" ]; then
        err "用法: ./manage.sh remove <slug>"
        exit 1
    fi
    ensure_csv
    if ! grep -q "^${slug}," "$CSV"; then
        err "slug='$slug' 不在 accounts.csv 中"
        exit 1
    fi
    log "停止并清理容器 c2a-${slug}..."
    if [ -f "$COMPOSE" ]; then
        dc stop "chat2api-${slug}" 2>/dev/null || true
        dc rm -f "chat2api-${slug}" 2>/dev/null || true
    fi
    log "从 accounts.csv 移除..."
    grep -v "^${slug}," "$CSV" > "$CSV.tmp" && mv "$CSV.tmp" "$CSV"
    log "保留 data/${slug}/ 目录（如确认无用，请手动 rm -rf）"
    cmd_apply
}

cmd_list() {
    require_compose
    dc ps
}

cmd_logs() {
    local slug="${1:-}" tail="${2:-200}"
    if [ -z "$slug" ]; then
        err "用法: ./manage.sh logs <slug> [行数]"
        exit 1
    fi
    docker logs --tail "$tail" -f "c2a-${slug}"
}

cmd_shell() {
    local slug="${1:-}"
    if [ -z "$slug" ]; then
        err "用法: ./manage.sh shell <slug>"
        exit 1
    fi
    docker exec -it "c2a-${slug}" sh
}

cmd_secrets() {
    if [ ! -f "$GEN_DIR/secrets.txt" ]; then
        err "secrets.txt 不存在，先 ./manage.sh init"
        exit 1
    fi
    cat "$GEN_DIR/secrets.txt"
    if [ -f "$GEN_DIR/orch.env" ]; then
        echo
        echo "============ Orchestrator 编排面板 ============"
        grep '^ORCH_PASSWORD=' "$GEN_DIR/orch.env" 2>/dev/null \
            | sed 's/^/  /'
        local port="${CHAT2API_GATEWAY_PORT:-60403}"
        echo "  URL: http://<vps>:${port}/orchestrator/"
        echo "==============================================="
    fi
}

cmd_orch_password() {
    if [ ! -f "$GEN_DIR/orch.env" ]; then
        err "orch.env 不存在；先 ./manage.sh init 生成"
        exit 1
    fi
    local new_pwd
    if [ -n "${1:-}" ]; then
        new_pwd="$1"
    else
        new_pwd=$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))')
    fi
    awk -v new="$new_pwd" '/^ORCH_PASSWORD=/{print "ORCH_PASSWORD="new; next} {print}' \
        "$GEN_DIR/orch.env" > "$GEN_DIR/orch.env.tmp"
    mv "$GEN_DIR/orch.env.tmp" "$GEN_DIR/orch.env"
    chmod 600 "$GEN_DIR/orch.env"
    log "已写入新 ORCH_PASSWORD，强制重建 orchestrator（让 env_file 重新加载）..."
    dc up -d --force-recreate orchestrator
    ok "新密码：$new_pwd"
    log "提示：HMAC SESSION_SECRET 未变，旧 cookie 仍有效到 8h 过期；如需立刻全部失效，编辑 orch.env 改 ORCH_SESSION_SECRET 后再次重建"
}

cmd_down() {
    require_compose
    dc down
    ok "已停止所有实例（数据保留）"
}

cmd_status() {
    require_compose
    log "容器状态:"
    dc ps
    echo
    log "出口 IP 抽样（前 5 个实例）:"
    local count=0
    for s in $(awk -F, 'NR>1 && $1!="" {print $1}' "$CSV"); do
        [ "$count" -ge 5 ] && break
        printf "  %-12s -> " "$s"
        docker exec "c2a-${s}" curl -s --max-time 8 https://api.ipify.org 2>/dev/null \
            || echo "(unreachable)"
        echo
        count=$((count+1))
    done
}

cmd_help() {
    cat <<'EOF'
chat2api 多实例运维（一容器一账号）

用法:
  ./manage.sh init                          首次部署（自动从 example 复制 csv）
  ./manage.sh apply                         编辑 accounts.csv 后重新应用
  ./manage.sh add <slug> [proxy] [note]     追加单个账号 + apply
  ./manage.sh remove <slug>                 移除单个账号 + apply（保留 data/）
  ./manage.sh list                          所有容器状态（docker compose ps）
  ./manage.sh status                        状态 + 抽样验证出口 IP
  ./manage.sh logs <slug> [N]               跟随该实例日志（默认 200 行）
  ./manage.sh shell <slug>                  进入该实例容器 shell
  ./manage.sh secrets                       打印所有 AUTH / ADMIN_PWD（敏感）
  ./manage.sh orch-password [pwd]           重置编排面板密码（不传则随机生成）
  ./manage.sh down                          停止全部（数据保留）
  ./manage.sh help                          显示本帮助

环境变量（可选）:
  CHAT2API_GATEWAY_PORT  nginx 对外端口（默认 60403）
  CHAT2API_IMAGE         覆盖镜像（默认 ghcr.io/nanashiwang/chat2api:latest）

文件:
  accounts.csv           真实账号清单（敏感，git 忽略）
  generated/             生成产物（含密钥，git 忽略）
  data/<slug>/           每实例数据卷（含 cookie/token，git 忽略）
EOF
}

cmd="${1:-help}"
shift || true
case "$cmd" in
    init)    cmd_init "$@" ;;
    apply)   cmd_apply "$@" ;;
    add)     cmd_add "$@" ;;
    remove)  cmd_remove "$@" ;;
    list)    cmd_list "$@" ;;
    status)  cmd_status "$@" ;;
    logs)    cmd_logs "$@" ;;
    shell)   cmd_shell "$@" ;;
    secrets) cmd_secrets "$@" ;;
    orch-password) cmd_orch_password "$@" ;;
    down)    cmd_down "$@" ;;
    help|-h|--help) cmd_help ;;
    *) err "未知命令: $cmd"; cmd_help; exit 1 ;;
esac
