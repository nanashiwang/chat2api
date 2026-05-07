#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="/etc/chat2api.env"

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1091
  source "$CONFIG_FILE"
fi

detect_install_dir() {
  if [[ -n "${INSTALL_DIR:-}" && -d "${INSTALL_DIR:-}" ]]; then
    printf "%s\n" "$INSTALL_DIR"
    return
  fi

  if [[ -f "./docker-compose.yml" || -f "./compose.yml" || -f "./compose.yaml" ]]; then
    pwd
    return
  fi

  local candidates=(
    "/opt/chat2api"
    "/opt/chat2api/data"
    "/srv/chat2api"
    "/root/chat2api"
    "$HOME/chat2api"
  )

  local dir
  for dir in "${candidates[@]}"; do
    if [[ -f "$dir/docker-compose.yml" || -f "$dir/compose.yml" || -f "$dir/compose.yaml" || -x "$dir/deploy/multi/manage.sh" ]]; then
      printf "%s\n" "$dir"
      return
    fi
  done

  return 1
}

INSTALL_DIR="$(detect_install_dir || true)"
if [[ -z "$INSTALL_DIR" ]]; then
  echo "Cannot find chat2api compose directory."
  echo "Run this command inside your deployment directory,"
  echo "or create /etc/chat2api.env,"
  echo "or run deploy/install.sh / deploy/install-command.sh first."
  exit 1
fi

cd "$INSTALL_DIR"

is_multi_install() {
  [[ -x "$INSTALL_DIR/deploy/multi/manage.sh" ]]
}

run_multi_manage() {
  (cd "$INSTALL_DIR/deploy/multi" && ./manage.sh "$@")
}

run_compose() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    sudo docker compose "$@"
  else
    echo "docker compose is not available"
    exit 1
  fi
}

show_help() {
  if is_multi_install; then
    cat <<'EOF'
Usage: chat2api <command>

Multi-instance commands:
  update      Re-generate config and recreate services
  start       Same as update
  restart     Same as update
  stop        Stop all multi-instance services
  status      Show multi-instance status and sampled egress IPs
  verify      Verify admin/tokens routing for all instances
  logs <slug> Tail one instance logs
  shell <slug> Enter one instance shell
  secrets     Print instance auth/admin secrets
  admin       Print orchestrator/admin entry hints
  path        Print install directory
  help        Show this help

Any other command is passed through to: deploy/multi/manage.sh
EOF
    return
  fi
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
    if is_multi_install; then
      run_multi_manage apply
    else
      run_compose pull
      run_compose up -d
    fi
    ;;
  restart)
    if is_multi_install; then
      run_multi_manage apply
    else
      run_compose restart
    fi
    ;;
  stop)
    if is_multi_install; then
      run_multi_manage down
    else
      run_compose stop
    fi
    ;;
  start)
    if is_multi_install; then
      run_multi_manage apply
    else
      run_compose up -d
    fi
    ;;
  status)
    if is_multi_install; then
      run_multi_manage status
    else
      run_compose ps
    fi
    ;;
  logs)
    if is_multi_install; then
      run_multi_manage logs "${@:2}"
    else
      run_compose logs -f chat2api
    fi
    ;;
  shell)
    if is_multi_install; then
      run_multi_manage shell "${@:2}"
    else
      echo "shell command is only available in multi-instance mode"
      exit 1
    fi
    ;;
  verify)
    if is_multi_install; then
      run_multi_manage verify
    else
      echo "verify command is only available in multi-instance mode"
      exit 1
    fi
    ;;
  secrets)
    if is_multi_install; then
      run_multi_manage secrets
    else
      echo "secrets command is only available in multi-instance mode"
      exit 1
    fi
    ;;
  admin)
    if is_multi_install; then
      run_multi_manage secrets
    elif [[ -n "${PORT:-}" && -n "${API_PREFIX:-}" ]]; then
      echo "http://<server-ip>:${PORT}/${API_PREFIX}/admin/login"
    else
      echo "PORT/API_PREFIX not found in /etc/chat2api.env"
    fi
    ;;
  api)
    if is_multi_install; then
      run_multi_manage secrets
    elif [[ -n "${PORT:-}" && -n "${API_PREFIX:-}" ]]; then
      echo "http://<server-ip>:${PORT}/${API_PREFIX}/v1/chat/completions"
    else
      echo "PORT/API_PREFIX not found in /etc/chat2api.env"
    fi
    ;;
  path)
    echo "$INSTALL_DIR"
    ;;
  help|--help|-h)
    show_help
    ;;
  *)
    if is_multi_install; then
      run_multi_manage "$command_name" "${@:2}"
    else
      echo "Unknown command: $command_name"
      show_help
      exit 1
    fi
    ;;
esac
