#!/usr/bin/env bash
# Pull latest from git and restart bot + worker.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
FROM_BOT=false
[[ "${1:-}" == "--from-bot" ]] && FROM_BOT=true

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

GIT_BRANCH="${DEPLOY_GIT_BRANCH:-main}"
LOG_FILE="${DEPLOY_LOG_FILE:-$DIR/deploy.log}"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] deploy: $*" | tee -a "$LOG_FILE"
}

notify_slack() {
  local text="$1"
  if [[ -z "${SLACK_BOT_TOKEN:-}" || -z "${SLACK_CHANNEL_ID:-}" ]]; then
    return 0
  fi
  local payload
  payload="$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": sys.argv[2]}))' "$SLACK_CHANNEL_ID" "$text")"
  curl -sS -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H "Content-type: application/json; charset=utf-8" \
    -d "$payload" >/dev/null || true
}

stop_pidfile() {
  local file="$1"
  local name="$2"
  [[ -f "$file" ]] || return 0
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    log "stopping $name (pid $pid)"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

start_worker() {
  [[ "${START_WORKER:-true}" == "true" ]] && return 0
  log "starting worker"
  nohup ./worker.sh >>"${WORKER_LOG_FILE:-worker.log}" 2>&1 &
  echo $! >"$DIR/.worker.pid"
  log "worker pid $(cat "$DIR/.worker.pid")"
}

start_bot() {
  log "starting bot"
  if [[ -d .venv ]]; then
    nohup .venv/bin/python bot.py >>"${BOT_LOG_FILE:-bot.log}" 2>&1 &
  else
    nohup python3 bot.py >>"${BOT_LOG_FILE:-bot.log}" 2>&1 &
  fi
  echo $! >"$DIR/.bot.pid"
  log "bot pid $(cat "$DIR/.bot.pid")"
}

run_deploy() {
  log "git pull origin $GIT_BRANCH"
  git pull origin "$GIT_BRANCH"
  local commit
  commit="$(git rev-parse --short HEAD)"

  if [[ -d .venv ]]; then
    log "updating venv dependencies"
    .venv/bin/pip install -q -r requirements.txt
  elif command -v pip3 >/dev/null 2>&1; then
    log "updating user dependencies"
    pip3 install --user -q -r requirements.txt 2>/dev/null || true
  fi

  stop_pidfile "$DIR/.worker.pid" "worker"
  start_worker

  notify_slack "$(python3 "$DIR/messages.py" deploy_success "$commit" "$GIT_BRANCH")"
  log "deploy complete at $commit"

  if [[ "${START_WORKER:-true}" == "true" ]]; then
    stop_pidfile "$DIR/.bot.pid" "bot"
    start_bot
  elif $FROM_BOT || [[ -f "$DIR/.bot.pid" ]]; then
    stop_pidfile "$DIR/.bot.pid" "bot"
    start_bot
  fi
}

main() {
  notify_slack "$(python3 "$DIR/messages.py" deploy_started)"
  if run_deploy >>"$LOG_FILE" 2>&1; then
    return 0
  fi
  notify_slack "$(python3 "$DIR/messages.py" deploy_failed "see deploy.log")"
  exit 1
}

main "$@"
