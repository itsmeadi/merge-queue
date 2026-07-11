#!/usr/bin/env bash
# Pull latest merge-queue code and restart bot + worker.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RESPONSE_URL="${1:-}"
DEPLOY_LOG="${SCRIPT_DIR}/deploy.log"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$DEPLOY_LOG"
}

slack_post() {
  local text="$1"
  if [[ -z "$RESPONSE_URL" ]]; then
    log "WARN: no response_url — cannot update deploy status in Slack"
    return 0
  fi
  local payload resp
  payload="$(python3 -c 'import json,sys; print(json.dumps({"replace_original": True, "text": sys.argv[1]}))' "$text")"
  resp="$(curl -sS -X POST "$RESPONSE_URL" \
    -H "Content-type: application/json; charset=utf-8" \
    -d "$payload")"
  if ! python3 -c 'import json,sys; r=json.loads(sys.argv[1]); sys.exit(0 if r.get("ok") is True else 1)' "$resp" 2>/dev/null; then
    log "WARN: failed to update deploy message: $resp"
  fi
}

kill_by_pattern() {
  local pattern="$1"
  pkill -f "$pattern" 2>/dev/null || true
  sleep 1
}

start_worker() {
  nohup "$SCRIPT_DIR/worker.sh" >>"$DEPLOY_LOG" 2>&1 &
  log "Started worker (pid $!)"
}

start_bot() {
  local python_cmd=(python3)
  if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
    python_cmd=("$SCRIPT_DIR/.venv/bin/python")
  fi
  nohup "${python_cmd[@]}" "$SCRIPT_DIR/bot.py" >>"$DEPLOY_LOG" 2>&1 &
  log "Started bot (pid $!)"
}

main() {
  log "Deploy started (branch: $DEPLOY_BRANCH)"

  if [[ ! -d "$SCRIPT_DIR/.git" ]]; then
    slack_post "Deploy failed — not a git repo at \`$SCRIPT_DIR\`"
    exit 1
  fi

  local before after pull_out
  before="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"
  log "Before: $before"

  if ! pull_out="$(git -C "$SCRIPT_DIR" pull origin "$DEPLOY_BRANCH" 2>&1)"; then
    log "git pull failed: $pull_out"
    slack_post "$(printf 'Deploy failed — git pull error:\n```%s```' "$pull_out")"
    exit 1
  fi
  log "$pull_out"

  after="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"

  if [[ -x "$SCRIPT_DIR/.venv/bin/pip" ]]; then
    log "Updating Python dependencies"
    "$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" >>"$DEPLOY_LOG" 2>&1 || true
  fi

  kill_by_pattern "$SCRIPT_DIR/worker.sh"
  if [[ "${START_WORKER:-true}" == "false" ]]; then
    start_worker
  fi

  kill_by_pattern "$SCRIPT_DIR/bot.py"
  start_bot

  if [[ "$before" == "$after" ]]; then
    slack_post "Deploy done — already up to date (\`$after\`). Restarted bot + worker."
  else
    slack_post "Deploy done — \`$before\` → \`$after\`. Restarted bot + worker."
  fi

  log "Deploy finished"
}

main "$@"
