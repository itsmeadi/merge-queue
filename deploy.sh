#!/usr/bin/env bash
# Pull latest merge-queue code and restart bot + worker.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SLACK_CHANNEL="${1:-${SLACK_CHANNEL_ID:-}}"
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
  if [[ -z "${SLACK_BOT_TOKEN:-}" || -z "$SLACK_CHANNEL" ]]; then
    return 0
  fi
  local payload resp ok
  payload="$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": sys.argv[2]}))' "$SLACK_CHANNEL" "$text")"
  resp="$(curl -sS -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H "Content-type: application/json; charset=utf-8" \
    -d "$payload")"
  ok="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok", False))' <<<"$resp" 2>/dev/null || echo False)"
  if [[ "$ok" != "True" ]]; then
    log "WARN: failed to post to Slack: $resp"
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
    slack_post ":x: Deploy failed — not a git repo at \`$SCRIPT_DIR\`"
    exit 1
  fi

  local before after pull_out=0
  before="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"
  log "Before: $before"

  if ! pull_out="$(git -C "$SCRIPT_DIR" pull origin "$DEPLOY_BRANCH" 2>&1)"; then
    log "git pull failed: $pull_out"
    slack_post ":x: Deploy failed — git pull error:\n\`\`\`$pull_out\`\`\`"
    exit 1
  fi
  log "$pull_out"

  after="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"

  if [[ -x "$SCRIPT_DIR/.venv/bin/pip" ]]; then
    log "Updating Python dependencies"
    "$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" >>"$DEPLOY_LOG" 2>&1 || true
  fi

  kill_by_pattern "$SCRIPT_DIR/worker.sh"
  start_worker

  if [[ "$before" == "$after" ]]; then
    slack_post ":information_source: Deploy done — already up to date (\`$after\`). Restarted bot + worker."
  else
    slack_post ":white_check_mark: Deploy done — \`$before\` → \`$after\`. Restarted bot + worker."
  fi

  kill_by_pattern "$SCRIPT_DIR/bot.py"
  start_bot

  log "Deploy finished"
}

main "$@"
