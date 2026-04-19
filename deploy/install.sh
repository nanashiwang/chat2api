#!/usr/bin/env bash
set -euo pipefail

DEFAULT_INSTALL_DIR="/opt/chat2api"
DEFAULT_IMAGE="ghcr.io/nanashiwang/chat2api:latest"
DEFAULT_PORT="60403"
DEFAULT_API_PREFIX="nanapi-2026-a1"
DEFAULT_GROUP_SIZE="25"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

prompt() {
  local var_name="$1"
  local prompt_text="$2"
  local default_value="${3:-}"
  local secret="${4:-false}"
  local current_value=""
  if [[ "$secret" == "true" ]]; then
    read -r -s -p "$prompt_text [$default_value]: " current_value
    echo
  else
    read -r -p "$prompt_text [$default_value]: " current_value
  fi
  if [[ -z "$current_value" ]]; then
    current_value="$default_value"
  fi
  printf -v "$var_name" '%s' "$current_value"
}

yaml_escape() {
  printf "%s" "$1" | sed "s/'/''/g"
}

ensure_docker() {
  if command_exists docker && docker compose version >/dev/null 2>&1; then
    return
  fi

  echo "Docker or docker compose plugin not found, installing..."
  if ! command_exists curl; then
    if command_exists apt-get; then
      sudo apt-get update
      sudo apt-get install -y curl
    else
      echo "curl is required to install Docker automatically."
      exit 1
    fi
  fi

  curl -fsSL https://get.docker.com | sudo sh
  sudo systemctl enable docker
  sudo systemctl start docker
}

extract_first_proxy_url() {
  local raw="$1"
  local first="${raw%%,*}"
  first="$(printf "%s" "$first" | xargs)"
  if [[ "$first" == *"|"* ]]; then
    first="${first#*|}"
  fi
  printf "%s" "$first"
}

echo "== Chat2API one-click installer =="
prompt INSTALL_DIR "Install directory" "$DEFAULT_INSTALL_DIR"
prompt IMAGE "Docker image" "$DEFAULT_IMAGE"
prompt PORT "Host port" "$DEFAULT_PORT"
prompt API_PREFIX "API prefix" "$DEFAULT_API_PREFIX"
prompt AUTHORIZATION "API authorization token" "sk-your-api-key" "true"
prompt ADMIN_PASSWORD "Admin password" "change-me-admin" "true"
prompt INIT_TOKENS "Initial tokens (comma separated)" "rt-xxx1,rt-xxx2"
prompt INIT_PROXIES "Initial proxies (comma separated, NAME|URL)" "IP-A|socks5://127.0.0.1:7890,IP-B|socks5://127.0.0.2:7890"
prompt INIT_GROUP_SIZE "Initial group size" "$DEFAULT_GROUP_SIZE"

FIRST_PROXY_URL="$(extract_first_proxy_url "$INIT_PROXIES")"
prompt EXPORT_PROXY_URL "Export proxy URL" "$FIRST_PROXY_URL"

mkdir -p "$INSTALL_DIR/data"
cd "$INSTALL_DIR"

cat > docker-compose.yml <<EOF
services:
  chat2api:
    image: '$(yaml_escape "$IMAGE")'
    container_name: chat2api
    restart: unless-stopped
    pull_policy: always
    ports:
      - '$(yaml_escape "$PORT"):5005'
    volumes:
      - ./data:/app/data
    environment:
      TZ: 'Asia/Shanghai'
      API_PREFIX: '$(yaml_escape "$API_PREFIX")'
      AUTHORIZATION: '$(yaml_escape "$AUTHORIZATION")'
      ADMIN_PASSWORD: '$(yaml_escape "$ADMIN_PASSWORD")'
      CHATGPT_BASE_URL: 'https://chatgpt.com'
      PROXY_URL: '$(yaml_escape "$(printf "%s" "$INIT_PROXIES" | sed 's/[[:space:]]*,[[:space:]]*/,/g' | sed 's/[^|,]*|//g')")'
      EXPORT_PROXY_URL: '$(yaml_escape "$EXPORT_PROXY_URL")'
      HISTORY_DISABLED: 'true'
      RETRY_TIMES: '3'
      RANDOM_TOKEN: 'false'
      SCHEDULED_REFRESH: 'false'
      ENABLE_LIMIT: 'true'
      CHECK_MODEL: 'false'
      UPLOAD_BY_URL: 'false'
      OAI_LANGUAGE: 'zh-CN'
      ENABLE_GATEWAY: 'false'
      AUTO_SEED: 'true'
      FORCE_NO_HISTORY: 'false'
      NO_SENTINEL: 'false'
      INIT_TOKENS: '$(yaml_escape "$INIT_TOKENS")'
      INIT_PROXIES: '$(yaml_escape "$INIT_PROXIES")'
      INIT_GROUP_SIZE: '$(yaml_escape "$INIT_GROUP_SIZE")'
      INIT_APPLY_ON_EMPTY: 'true'
      INIT_FORCE: 'false'
    healthcheck:
      test: ["CMD-SHELL", "python3 - <<'PY'\\nimport urllib.request\\nurllib.request.urlopen('http://127.0.0.1:5005/$(yaml_escape "$API_PREFIX")/admin/login', timeout=5)\\nprint('ok')\\nPY"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
EOF

ensure_docker

sudo mkdir -p /etc
sudo tee /etc/chat2api.env >/dev/null <<EOF
INSTALL_DIR='$(yaml_escape "$INSTALL_DIR")'
PORT='$(yaml_escape "$PORT")'
API_PREFIX='$(yaml_escape "$API_PREFIX")'
IMAGE='$(yaml_escape "$IMAGE")'
EOF

SCRIPT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo install -m 0755 "$SCRIPT_SOURCE_DIR/chat2api.sh" /usr/local/bin/chat2api

sudo docker compose pull
sudo docker compose up -d

echo
echo "Deployment complete."
echo "Admin login: http://<server-ip>:$PORT/$API_PREFIX/admin/login"
echo "API endpoint: http://<server-ip>:$PORT/$API_PREFIX/v1/chat/completions"
echo "Manage commands: chat2api status | chat2api logs | chat2api update"
