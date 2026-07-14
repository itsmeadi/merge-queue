# Merge Queue

Standalone Slack-driven FIFO merge queue. Copy this entire folder to any host (e.g. EC2) and run.

Queue PRs via Slack → sync with master → wait for CI (rerun on failure) → merge when green.

## Folder contents

```
merge-queue/
├── bot.py                  # Slack bot (Socket Mode)
├── pr_extract.py           # PR URL extraction from Slack messages
├── worker.sh               # Merge worker (gh CLI)
├── deploy.sh               # git pull + restart (for /merge-deploy)
├── start.sh                # One-command startup
├── requirements.txt        # Python deps (slack-bolt only)
├── slack-app-manifest.yaml # Create the Slack app from this
├── .env.example            # Copy to .env with your tokens
├── prs.txt.example         # Queue file format
└── README.md
```

Runtime files (created automatically): `prs.txt`, `prs-failed.txt`, `prs-skipped.txt`, `prs-merged.txt`, `.venv/`

## Prerequisites on the host

- Python 3.9+
- [GitHub CLI](https://cli.github.com/) **2.50+** (`gh pr update-branch` required) — install from https://cli.github.com/, not old distro packages
- `gh` authenticated: `GH_TOKEN` or `gh auth login` (classic PAT: `repo` + `read:org`, SSO authorized for GetStream)
- Slack app created from `slack-app-manifest.yaml`

## Quick start (EC2 or local)

```bash
# 1. Copy folder to host
scp -r merge-queue/ ec2-host:~/

# 2. On the host
cd ~/merge-queue
cp .env.example .env
# edit .env with your Slack tokens and channel ID

chmod +x start.sh worker.sh
./start.sh
```

Keep it running with `tmux`, `screen`, or systemd.

## Slack setup

1. https://api.slack.com/apps → Create New App → From manifest → paste `slack-app-manifest.yaml`
2. App-Level Tokens → Generate → scope `connections:write` (for Socket Mode)
3. Install App → copy `xoxb-...` bot token
4. Upload app icon: `icon-256.png` or `icon.png` (Basic Information → App Icon)
5. `/invite @merge-bot` in your channel
6. **Custom emojis** (workspace admin): upload `icon-256.png` as `:merge_bot:` and a green “merged” badge as `:merged:` (see [Emoji reactions](#emoji-reactions))
7. After manifest updates: **reinstall the app** so new scopes/events apply

## Slack commands

| Command | Description |
|---------|-------------|
| `/merge 12345` | Queue a PR (number or full URL); rejects immediately if PR is missing, closed/merged, conflicting, or not approved |
| `/merge-queue 12345` | Same as `/merge` |
| `/merge-remove 12345` | Remove a PR from the queue (won't stop one already merging) |
| `/merge-status` | Show current queue |
| `/merge-history` | Show last 5 completed PRs (optional count, max 50) |
| `/merge-deploy` | Pull from git and restart bot + worker (allowlisted users only) |

## Emoji reactions

React with **`:merge_bot:`** on any message that contains a `GetStream/chat` PR link (GitHub notifications, pasted URLs, etc.) to queue it — same preflight as `/merge`.

| Emoji | Who | When |
|-------|-----|------|
| `:merge_bot:` | You | Queue the PR |
| `:white_check_mark:` | Bot | Queued successfully |
| `:x:` | Bot | Preflight rejected |
| `:ghost:` | Bot | No PR link found in message |
| `:merged:` | Bot | PR squash-merged (on the original message) |

Upload custom emojis: Slack → **Customize workspace** → **Add custom emoji**
- `:merge_bot:` — use `icon-256.png` (same as the app icon)
- `:merged:` — use a green “merged” badge (add your own `emoji-merged.png`)

Configure in `.env` if names differ: `MERGE_REACTION_EMOJI`, `MERGED_REACTION_EMOJI`.

### Troubleshooting emoji reactions

1. **Reinstall the Slack app** after updating `slack-app-manifest.yaml` — new scopes (`reactions:read`, `reactions:write`) and `reaction_added` event only apply after reinstall (https://api.slack.com/apps → your app → Install App).
2. **Emoji name must match exactly** — if Slack shows `:merge-bot:` the env is `MERGE_REACTION_EMOJI=merge-bot` (no colons). Check startup log: `Emoji queue trigger: :merge_bot:`.
3. **`@merge-bot` must be in the channel** where you react.
4. **Watch `bot.log`** while reacting — you should see `reaction_added: :merge_bot: on C.../...`. If nothing appears, the app is not receiving events (reinstall / duplicate bot processes).
5. **One bot only** — `ps aux | grep bot.py` should show a single process.

```bash
tail -f ~/merge-queue/bot.log
# react :merge_bot: on a PR message — expect log lines within 1s
```

## Deploy from Slack

Enable in `.env`:

```bash
DEPLOY_ENABLED=true
DEPLOY_ALLOWED_USER_IDS=U01234567   # your Slack member ID
DEPLOY_BRANCH=main
```

Find your Slack user ID: profile → ⋮ → **Copy member ID**.

Prerequisites on the host:

- Clone from git: `git clone https://github.com/itsmeadi/merge-queue.git ~/mergebot`
- `git pull` works non-interactively (deploy key or credential helper)
- Bot and worker run from the same install dir

First time: `git pull` manually once to get `deploy.sh`, then use `/merge-deploy` in Slack.

Logs: `deploy.log` in the install dir.

## How it works

```
/merge (Slack) → bot.py writes prs.txt
                      ↓
                 worker.sh polls prs.txt every 10s
                      ↓
     sync with base (if BEHIND) → wait for CI on HEAD → squash merge
                      ↓
     skipped → prs-skipped.txt  |  CI exhausted → prs-failed.txt
                      ↓
            worker continues with next PR in queue
```

Worker behavior:

- Detects **BEHIND** (`mergeStateStatus`) and runs `gh pr update-branch` before trusting CI
- Waits for CI on the **current HEAD commit** (not stale green checks from an old push)
- Re-syncs if the PR falls behind again while waiting
- Defaults to **squash merge** (required for `GetStream/chat`)
- **Skips** non-retryable PRs (missing approval, merge conflict, branch protection) → `prs-skipped.txt`, continues queue
- **Retries** only flaky CI failures (`gh run rerun`, up to `MAX_RETRIES`, default 3)
- **CI failures** post as threaded replies under the original `/merge` message with failed check names and a log excerpt

- **bot.py** listens to Slack, manages the queue file, starts **worker.sh**
- **worker.sh** does the git/GitHub work via `gh` CLI
- No npm, no dependency on any other repo

## Configuration (.env)

| Variable | Default | Purpose |
|----------|---------|---------|
| `SLACK_BOT_TOKEN` | — | Bot OAuth token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | — | App-level token for Socket Mode (`xapp-...`) |
| `SLACK_CHANNEL_ID` | — | Channel for worker status posts |
| `DEFAULT_REPO` | `GetStream/chat` | Default repo for `/merge 12345` |
| `MAX_RETRIES` | `3` | CI rerun attempts per PR |
| `POLL_INTERVAL` | `10` | Seconds to wait when queue is empty |
| `CHECK_INTERVAL` | `10` | CI poll interval |
| `MERGE_METHOD` | `squash` | `merge`, `squash`, or `rebase` |
| `CI_HEAD_WAIT_MAX` | `3600` | Max seconds to wait for CI on HEAD commit |
| `CI_SETTLE_AFTER_SYNC` | `45` | Seconds to wait after `update-branch` before trusting CI status |
| `REQUIRED_CHECK` | `Ready to merge` | Required GitHub check that must pass before merge |
| `MERGE_QUEUE_DIR` | install dir | Queue + history files live next to `bot.py` |
| `PR_PROCESSING_FILE` | `.../processing.txt` | PR currently being processed (shown in `/merge-status`) |
| `PR_THREADS_FILE` | `.../prs-threads.json` | Maps queued PR URLs to Slack thread anchors for CI updates |
| `PR_SKIPPED_FILE` | `.../prs-skipped.txt` | Skipped PRs (approval, conflict, policy) |
| `PR_MERGED_FILE` | `.../prs-merged.txt` | Successfully merged PRs |
| `START_WORKER` | `true` | Set `false` to run worker separately |
| `DEPLOY_ENABLED` | `false` | Enable `/merge-deploy` |
| `DEPLOY_ALLOWED_USER_IDS` | — | Comma-separated Slack user IDs allowed to deploy |
| `DEPLOY_BRANCH` | `main` | Branch to pull on deploy |
| `MERGE_REACTION_EMOJI` | `merge_bot` | Custom emoji name to react with and queue a PR |
| `MERGED_REACTION_EMOJI` | `merged` | Custom emoji bot adds to source message when PR merges |
| `MERGE_REACTION_ACK` | `true` | Bot adds ack reactions on emoji enqueue |

## Skip vs failed

| Outcome | File | Examples |
|---------|------|----------|
| **Skipped** | `prs-skipped.txt` | Missing approval, changes requested, merge conflict, branch protection |
| **Failed** | `prs-failed.txt` | CI failed after max reruns, unexpected merge error |
| **Merged** | `prs-merged.txt` | Successfully squash-merged |

View recent outcomes in Slack: `/merge-history` (default last 5).

Re-queue a skipped PR after approval:

```bash
echo 'https://github.com/GetStream/chat/pull/12345' >> ~/merge-queue/prs.txt
```

## Run worker standalone

```bash
source .env
./worker.sh              # continuous loop
./worker.sh --once       # process one PR and exit
```

## systemd example

```ini
[Unit]
Description=Merge Queue Bot
After=network.target

[Service]
Type=simple
User=stream
WorkingDirectory=/home/stream/merge-queue
EnvironmentFile=/home/stream/merge-queue/.env
ExecStart=/home/stream/merge-queue/.venv/bin/python /home/stream/merge-queue/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
