#!/usr/bin/env bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

SSH_HOST_DEFAULT="root@172.234.237.195"
REMOTE_DIR_DEFAULT="/var/www/nesquena-hermes-webui"
BRANCH_DEFAULT="master"
SERVICE_DEFAULT="hermes-webui"
HEALTH_WAIT_SECONDS=60

SSH_HOST="$SSH_HOST_DEFAULT"
REMOTE_DIR="$REMOTE_DIR_DEFAULT"
BRANCH="$BRANCH_DEFAULT"
SERVICE="$SERVICE_DEFAULT"
SKIP_SMOKE=0

print_help() {
    cat <<EOF
用法: $(basename "$0") [选项]

在 172 生产机上拉取指定分支并只重建 hermes-webui 容器。
完整 runbook、回滚和冲突处理见 docs/deploy-172.md。

选项:
  --host <user@host>     SSH 目标 (默认: ${SSH_HOST_DEFAULT})
  --dir <path>           服务器仓库目录 (默认: ${REMOTE_DIR_DEFAULT})
  --branch <name>        拉取分支 (默认: ${BRANCH_DEFAULT})
  --service <name>       compose 服务名 (默认: ${SERVICE_DEFAULT})
  --skip-smoke           跳过部署后 smoke check
  -h, --help             显示帮助

环境变量:
  DEPLOY_SSH_HOST, DEPLOY_REMOTE_DIR, DEPLOY_BRANCH 可覆盖上述默认值
EOF
}

require_value() {
    local option="$1"
    local value="${2:-}"
    if [[ -z "$value" || "$value" == --* ]]; then
        log_error "$option 缺少参数值"
        print_help
        exit 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            require_value "$1" "${2:-}"
            SSH_HOST="$2"
            shift 2
            ;;
        --dir)
            require_value "$1" "${2:-}"
            REMOTE_DIR="$2"
            shift 2
            ;;
        --branch)
            require_value "$1" "${2:-}"
            BRANCH="$2"
            shift 2
            ;;
        --service)
            require_value "$1" "${2:-}"
            SERVICE="$2"
            shift 2
            ;;
        --skip-smoke)
            SKIP_SMOKE=1
            shift
            ;;
        -h | --help)
            print_help
            exit 0
            ;;
        *)
            log_error "未知参数: $1"
            print_help
            exit 1
            ;;
    esac
done

SSH_HOST="${DEPLOY_SSH_HOST:-$SSH_HOST}"
REMOTE_DIR="${DEPLOY_REMOTE_DIR:-$REMOTE_DIR}"
BRANCH="${DEPLOY_BRANCH:-$BRANCH}"

run_ssh() {
    ssh -o BatchMode=yes "$SSH_HOST" "$@"
}

shell_quote() {
    local value=${1//\'/\'\\\'\'}
    printf "'%s'" "$value"
}

run_ssh_bash() {
    local remote_command="bash -s --"
    local arg
    for arg in "$@"; do
        remote_command+=" $(shell_quote "$arg")"
    done
    ssh -o BatchMode=yes "$SSH_HOST" "$remote_command"
}

log_info "Step 1/4 确认 SSH 可达: $SSH_HOST"
run_ssh 'hostname; whoami'
log_success "SSH 连接正常"

log_info "Step 2/4 更新源码并重建 $SERVICE ..."
run_ssh_bash "$REMOTE_DIR" "$BRANCH" "$SERVICE" <<'REMOTE_SCRIPT'
set -euo pipefail

REMOTE_DIR="$1"
BRANCH="$2"
SERVICE="$3"

cd "$REMOTE_DIR"
ensure_clean_checkout() {
  local phase="$1"
  local status_output
  status_output=$(git status --short --untracked-files=normal)
  if [[ -n "$status_output" ]]; then
    echo "[ERROR] 服务器仓库存在未提交或未跟踪改动，停止自动部署 ($phase)" >&2
    echo "$status_output" >&2
    echo '请按 docs/deploy-172.md 手工核对；确认不是生产临时改动后再处理或加入 ignore。' >&2
    exit 1
  fi
}
ensure_head_matches_origin() {
  local head_commit
  local origin_commit
  head_commit=$(git rev-parse HEAD)
  origin_commit=$(git rev-parse "origin/$BRANCH")
  if [[ "$head_commit" != "$origin_commit" ]]; then
    echo "[ERROR] 服务器 HEAD 与 origin/$BRANCH 不一致，停止自动部署" >&2
    echo "HEAD=$head_commit" >&2
    echo "origin/$BRANCH=$origin_commit" >&2
    echo '请按 docs/deploy-172.md 手工核对服务器分支状态。' >&2
    exit 1
  fi
}
current_branch=$(git branch --show-current)
if [[ "$current_branch" != "$BRANCH" ]]; then
  echo "[ERROR] 服务器当前分支不是 $BRANCH，停止自动部署" >&2
  echo "current_branch=$current_branch" >&2
  echo '请按 docs/deploy-172.md 手工核对服务器分支状态。' >&2
  exit 1
fi
ensure_clean_checkout 'before pull'
if ! git fetch origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"; then
  GIT_PROTOCOL=version=1 git fetch origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
fi
git status --short
git log --oneline -1 HEAD
git log --oneline -1 "origin/$BRANCH"
git merge --ff-only "origin/$BRANCH"
ensure_clean_checkout 'after fast-forward'
ensure_head_matches_origin
docker compose up -d --build "$SERVICE"
REMOTE_SCRIPT
log_success "容器已重建"

if [[ "$SKIP_SMOKE" -eq 1 ]]; then
    log_warning "已跳过 smoke check"
    log_success "部署完成: $SSH_HOST:$REMOTE_DIR ($SERVICE)"
    exit 0
fi

log_info "Step 3/4 等待 $SERVICE 健康 (最多 ${HEALTH_WAIT_SECONDS}s) ..."
run_ssh_bash "$SERVICE" "$HEALTH_WAIT_SECONDS" <<'REMOTE_SCRIPT'
set -euo pipefail

SERVICE="$1"
HEALTH_WAIT_SECONDS="$2"

deadline=$((SECONDS + HEALTH_WAIT_SECONDS))
health=starting
health_http=000
while (( SECONDS < deadline )); do
  health=$(docker inspect "$SERVICE" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo missing)
  health_http=$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:8787/health || echo 000)
  if [[ "$health" == healthy && "$health_http" == 200 ]]; then
    break
  fi
  sleep 2
done
echo "health=$health health_http=$health_http"
if [[ "$health" != healthy || "$health_http" != 200 ]]; then
  echo '[ERROR] 健康检查超时' >&2
  docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}' | grep "$SERVICE" || true
  exit 1
fi
REMOTE_SCRIPT
log_success "健康检查通过"

log_info "Step 4/4 smoke check ..."
run_ssh_bash "$SERVICE" <<'REMOTE_SCRIPT'
set -euo pipefail

SERVICE="$1"

check_cors_preflight() {
  local label="$1"
  local url="$2"
  local headers="/tmp/hermes-${label}-cors.headers"
  local http
  http=$(curl -sS --max-time 8 -D "$headers" -o /dev/null -w '%{http_code}' -X OPTIONS "$url" -H 'Origin: http://localhost:5173' -H 'Access-Control-Request-Method: POST' || echo 000)
  echo "${label}_cors_http=$http"
  if [[ "$http" != 204 ]]; then
    echo "[ERROR] ${label} CORS preflight 检查失败" >&2
    exit 1
  fi
  if ! grep -qi '^Access-Control-Allow-Credentials:[[:space:]]*true' "$headers"; then
    echo "[ERROR] ${label} CORS preflight 缺少 Access-Control-Allow-Credentials: true" >&2
    exit 1
  fi
  if ! grep -qi '^Access-Control-Allow-Methods:.*POST' "$headers"; then
    echo "[ERROR] ${label} CORS preflight 缺少允许 POST 的 Access-Control-Allow-Methods" >&2
    exit 1
  fi
}
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}' | grep "^${SERVICE}|"
docker inspect "$SERVICE" --format 'project={{ index .Config.Labels "com.docker.compose.project" }} service={{ index .Config.Labels "com.docker.compose.service" }} working_dir={{ index .Config.Labels "com.docker.compose.project.working_dir" }}'
curl -sS --max-time 8 -o /tmp/hermes-health.json -w 'health_http=%{http_code}\n' http://127.0.0.1:8787/health
python3 -m json.tool /tmp/hermes-health.json | head -5
check_cors_preflight local http://127.0.0.1:8787/api/auth/token-login
public_health_http=$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' http://172.234.237.195:8787/health || echo 000)
echo "public_health_http=$public_health_http"
if [[ "$public_health_http" != 200 ]]; then
  echo '[ERROR] 公网 health 检查失败' >&2
  exit 1
fi
check_cors_preflight public http://172.234.237.195:8787/api/auth/token-login
REMOTE_SCRIPT
log_success "smoke check 通过"

log_success "部署完成: $SSH_HOST:$REMOTE_DIR ($SERVICE)"
