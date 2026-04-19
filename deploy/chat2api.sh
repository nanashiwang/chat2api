#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="/etc/chat2api.env"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Config file not found: $CONFIG_FILE"
  echo "Please run deploy/install.sh first."
  exit 1
fi

# shellcheck disable=SC1091
source "$CONFIG_FILE"

if [[ -z "${INSTALL_DIR:-}" ]]; then
  echo "INSTALL_DIR is not set in $CONFIG_FILE"
  exit 1
fi

cd "$INSTALL_DIR"

run_compose() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    sudo docker compose "$@"
  else
    echo "docker compose is not available"
    exit 1
  fi
}

show_help() {
  cat <<'EOF'
Usage: chat2api <command>

Commands:
  update      Pull latest image and recreate containers
  restart     Restart services
  stop        Stop services
  start       Start services
  status      Show compose status
  logs        Tail chat2api logs
  admin       Print admin login URL
  api         Print API base URL
  path        Print install directory
  help        Show this help
EOF
}

command_name="${1:-help}"

case "$command_name" in
  update)
    run_compose pull
    run_compose up -d
    ;;
  restart)
    run_compose restart
    ;;
  stop)
    run_compose stop
    ;;
  start)
    run_compose up -d
    ;;
  status)
    run_compose ps
    ;;
  logs)
    run_compose logs -f chat2api
    ;;
  admin)
    echo "http://<server-ip>:${PORT}/${API_PREFIX}/admin/login"
    ;;
  api)
    echo "http://<server-ip>:${PORT}/${API_PREFIX}/v1/chat/completions"
    ;;
  path)
    echo "$INSTALL_DIR"
    ;;
  help|--help|-h)
    show_help
    ;;
  *)
    echo "Unknown command: $command_name"
    show_help
    exit 1
    ;;
esac
