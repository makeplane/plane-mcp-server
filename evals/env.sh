#!/usr/bin/env bash
# Local eval environment bootstrap: plane-ee API + mock feature-flag server.
#
# Required env (no defaults):
#   PLANE_EE_API_DIR  — path to plane-ee apps/api (or monorepo root with apps/api)
#   PLANE_EE_VENV    — path to the Python venv used to run plane-ee manage.py
#
# Mock flags are launched via the *repo* venv (REPO/.venv/bin/python -m evals.mock_flags),
# not PLANE_EE_VENV — that venv only runs plane-ee. Both FEATURE_FLAG_SERVER_BASE_URL
# and health checks use http://127.0.0.1:9911 (mock binds 127.0.0.1).
#
# Usage:
#   evals/env.sh up      # start API :8000 + mock flags :9911; write evals/.env-pids
#   evals/env.sh down    # kill PIDs from evals/.env-pids (identity-checked)
#   evals/env.sh status  # report liveness
#
# API is launched with --noreload, API_KEY_RATE_LIMIT=5000/min, and
# FEATURE_FLAG_SERVER_BASE_URL=http://127.0.0.1:9911. Sources $PLANE_EE_API_DIR/.env
# (or apps/api/.env) with set -a.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$SCRIPT_DIR/.env-pids"
API_PORT=8000
FLAG_PORT=9911
FLAG_URL="http://127.0.0.1:${FLAG_PORT}"
API_URL="http://127.0.0.1:${API_PORT}"
FLAG_BASE_URL="http://127.0.0.1:${FLAG_PORT}"
# Repo venv used for evals.mock_flags (parameterized relative to this script).
MOCK_FLAGS_PYTHON="${REPO_ROOT}/.venv/bin/python"

die() { echo "error: $*" >&2; exit 1; }

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    die "$name is required (no default)"
  fi
}

resolve_api_dir() {
  require_env PLANE_EE_API_DIR
  require_env PLANE_EE_VENV
  local base="${PLANE_EE_API_DIR}"
  if [[ -d "$base/apps/api" ]]; then
    echo "$base/apps/api"
  elif [[ -d "$base" ]]; then
    echo "$base"
  else
    die "PLANE_EE_API_DIR not a directory: $base"
  fi
}

resolve_python() {
  local venv="${PLANE_EE_VENV}"
  if [[ -x "$venv/bin/python" ]]; then
    echo "$venv/bin/python"
  elif [[ -x "$venv" ]]; then
    echo "$venv"
  else
    die "PLANE_EE_VENV has no bin/python: $venv"
  fi
}

# True if $1 is a live pid whose command line looks like our managed process.
pid_is_ours() {
  local pid="$1"
  local kind="$2"  # flag | api
  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  local cmd
  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  if [[ -z "$cmd" ]]; then
    return 1
  fi
  case "$kind" in
    flag)
      [[ "$cmd" == *evals.mock_flags* ]] || [[ "$cmd" == *mock_flags* ]]
      ;;
    api)
      [[ "$cmd" == *manage.py*runserver* ]] || [[ "$cmd" == *"manage.py"*"runserver"* ]]
      ;;
    *)
      return 1
      ;;
  esac
}

cmd_up() {
  local api_dir py
  api_dir="$(resolve_api_dir)"
  py="$(resolve_python)"

  if [[ ! -x "$MOCK_FLAGS_PYTHON" ]]; then
    die "mock-flags python not found/executable: $MOCK_FLAGS_PYTHON (create repo .venv)"
  fi

  if [[ -f "$PID_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$PID_FILE"
    local live=0
    if [[ -n "${flag_pid:-}" ]] && kill -0 "$flag_pid" 2>/dev/null; then
      live=1
    fi
    if [[ -n "${api_pid:-}" ]] && kill -0 "$api_pid" 2>/dev/null; then
      live=1
    fi
    if [[ $live -eq 1 ]]; then
      die "already up (run 'down' first) — pidfile $PID_FILE has live process(es)"
    fi
    echo "warning: stale pidfile $PID_FILE (no live pids); replacing" >&2
    rm -f "$PID_FILE"
  fi

  # Mock flag server first (API may call it on boot).
  local flag_log="$SCRIPT_DIR/.mock_flags.log"
  (
    cd "$REPO_ROOT"
    export PLANE_EE_API_DIR
    exec "$MOCK_FLAGS_PYTHON" -m evals.mock_flags "$FLAG_PORT"
  ) >"$flag_log" 2>&1 &
  local flag_pid=$!
  echo "started mock_flags pid=$flag_pid log=$flag_log"
  # Record flag_pid immediately so a later failure does not strand :9911.
  {
    echo "flag_pid=$flag_pid"
    echo "flag_port=$FLAG_PORT"
  } >"$PID_FILE"

  # Source plane-ee .env
  local env_file=""
  if [[ -f "$api_dir/.env" ]]; then
    env_file="$api_dir/.env"
  elif [[ -f "$PLANE_EE_API_DIR/.env" ]]; then
    env_file="$PLANE_EE_API_DIR/.env"
  fi
  if [[ -n "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
    echo "sourced $env_file"
  else
    echo "warning: no .env found under $api_dir or $PLANE_EE_API_DIR" >&2
  fi

  local api_log="$SCRIPT_DIR/.api_runserver.log"
  (
    cd "$api_dir"
    export API_KEY_RATE_LIMIT="5000/min"
    export FEATURE_FLAG_SERVER_BASE_URL="${FLAG_BASE_URL}"
    # --noreload: $! must be the real server, not the autoreloader parent.
    exec "$py" manage.py runserver --noreload "0.0.0.0:${API_PORT}"
  ) >"$api_log" 2>&1 &
  local api_pid=$!
  echo "started api runserver pid=$api_pid log=$api_log"
  {
    echo "flag_pid=$flag_pid"
    echo "api_pid=$api_pid"
    echo "flag_port=$FLAG_PORT"
    echo "api_port=$API_PORT"
  } >"$PID_FILE"

  # Health checks (retry briefly)
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf -o /dev/null -X POST "$FLAG_URL/api/feature-flags/" \
      -H 'Content-Type: application/json' -d '{}'; then
      echo "health: mock flags :$FLAG_PORT OK"
      break
    fi
    if [[ $i -eq 10 ]]; then
      die "mock flags health check failed on :$FLAG_PORT (see $flag_log)"
    fi
    sleep 0.5
  done

  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
    if curl -sf -o /dev/null "$API_URL/" || curl -sf -o /dev/null "$API_URL/api/"; then
      echo "health: api :$API_PORT OK"
      break
    fi
    # Accept any HTTP response as "up" (auth redirects still mean listening).
    if curl -s -o /dev/null -w "%{http_code}" "$API_URL/" | grep -qE '^[2345]'; then
      echo "health: api :$API_PORT listening"
      break
    fi
    if [[ $i -eq 30 ]]; then
      die "api health check failed on :$API_PORT (see $api_log)"
    fi
    sleep 1
  done

  echo "env up: pids in $PID_FILE"
}

cmd_down() {
  if [[ ! -f "$PID_FILE" ]]; then
    echo "no $PID_FILE — nothing to stop"
    return 0
  fi
  # shellcheck disable=SC1090
  source "$PID_FILE"
  for name_kind in flag_pid:flag api_pid:api; do
    local name="${name_kind%%:*}"
    local kind="${name_kind##*:}"
    local pid="${!name:-}"
    if [[ -z "$pid" ]]; then
      echo "$name=unset"
      continue
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$name=$pid not running"
      continue
    fi
    if ! pid_is_ours "$pid" "$kind"; then
      echo "warning: refusing to kill $name=$pid — command line is not a managed evals process" >&2
      ps -o command= -p "$pid" 2>/dev/null | sed 's/^/  cmd: /' >&2 || true
      continue
    fi
    kill "$pid" 2>/dev/null || true
    sleep 0.3
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "stopped $name=$pid"
  done
  rm -f "$PID_FILE"
  echo "env down"
}

cmd_status() {
  local flag_ok=0 api_ok=0
  if curl -sf -o /dev/null -X POST "$FLAG_URL/api/feature-flags/" \
    -H 'Content-Type: application/json' -d '{}' 2>/dev/null; then
    flag_ok=1
  fi
  local code
  code="$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/" 2>/dev/null || echo 000)"
  if [[ "$code" =~ ^[2345] ]]; then
    api_ok=1
  fi
  echo "mock_flags :$FLAG_PORT  $([[ $flag_ok -eq 1 ]] && echo UP || echo DOWN)"
  echo "api        :$API_PORT  $([[ $api_ok -eq 1 ]] && echo UP || echo DOWN) (http $code)"
  if [[ -f "$PID_FILE" ]]; then
    echo "pid file: $PID_FILE"
    cat "$PID_FILE"
  else
    echo "pid file: (none)"
  fi
  [[ $flag_ok -eq 1 && $api_ok -eq 1 ]]
}

usage() {
  cat <<EOF
Usage: evals/env.sh {up|down|status}

  up      Start mock flags (:$FLAG_PORT) + plane-ee runserver (:$API_PORT)
  down    Kill processes recorded in evals/.env-pids (identity-checked)
  status  Report health of both services

Requires PLANE_EE_API_DIR and PLANE_EE_VENV (no defaults).
Mock flags python: $MOCK_FLAGS_PYTHON
Flag base URL: $FLAG_BASE_URL
EOF
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    -h|--help|help|"") usage; [[ -n "$cmd" ]] || exit 2 ;;
    *) die "unknown command: $cmd" ;;
  esac
}

main "$@"
