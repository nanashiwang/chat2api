#!/usr/bin/env bash
set -euo pipefail

DEFAULT_INSTALL_DIR="/opt/chat2api"
DEFAULT_PORT="60403"
DEFAULT_API_PREFIX="nanapi-2026-a1"

prompt() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="${3:-}"
  local current_value=""
  read -r -p "$prompt_text [$default_value]: " current_value
  if [[ -z "$current_value" ]]; then
    current_value="$default_value"
  fi
  printf -v "$var_name" '%s' "$current_value"
}

yaml_escape() {
  printf "%s" "$1" | sed "s/'/''/g"
}

echo "== Install chat2api manage command =="
prompt INSTALL_DIR "Compose directory" "$DEFAULT_INSTALL_DIR"
prompt PORT "Host port" "$DEFAULT_PORT"
prompt API_PREFIX "API prefix" "$DEFAULT_API_PREFIX"

if [[ ! -f "$INSTALL_DIR/docker-compose.yml" && ! -f "$INSTALL_DIR/compose.yml" && ! -f "$INSTALL_DIR/compose.yaml" ]]; then
  echo "No docker compose file found in: $INSTALL_DIR"
  exit 1
fi

SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo mkdir -p /etc
sudo tee /etc/chat2api.env >/dev/null <<EOF
INSTALL_DIR='$(yaml_escape "$INSTALL_DIR")'
PORT='$(yaml_escape "$PORT")'
API_PREFIX='$(yaml_escape "$API_PREFIX")'
EOF

sudo install -m 0755 "$SCRIPT_SOURCE_DIR/chat2api.sh" /usr/local/bin/chat2api

echo
echo "Installed."
echo "Try: chat2api status"
echo "Then: chat2api update"
