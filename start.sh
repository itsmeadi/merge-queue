#!/usr/bin/env bash
# Bootstrap venv (if needed) and start the merge-queue bot + worker.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${SLACK_BOT_TOKEN:-}" || -z "${SLACK_APP_TOKEN:-}" ]]; then
  echo "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env (see .env.example)"
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

# shellcheck disable=SC1091
source .venv/bin/activate
exec python bot.py
