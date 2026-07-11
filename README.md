# Merge Queue

Standalone Slack-driven FIFO merge queue. Copy this entire folder to any host (e.g. EC2) and run.

Queue PRs via Slack → sync with master → wait for CI (rerun on failure) → merge when green.

## Folder contents

```
merge-queue/
├── bot.py                  # Slack bot (Socket Mode)
├── worker.sh               # Merge worker (gh CLI)
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

## Slack commands

| Command | Description |
|---------|-------------|
| `/merge 12345` | Queue a PR (number or full URL) |
| `/merge-status` | Show current queue |
| `/merge-history` | Show last 5 completed PRs (optional count, max 50) |

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
- **Retries** only flaky CI failures (`gh run rerun`, up to `MAX_RETRIES`)

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
| `MAX_RETRIES` | `5` | CI rerun attempts per PR |
| `POLL_INTERVAL` | `10` | Seconds to wait when queue is empty |
| `CHECK_INTERVAL` | `10` | CI poll interval |
| `MERGE_METHOD` | `squash` | `merge`, `squash`, or `rebase` |
| `CI_HEAD_WAIT_MAX` | `3600` | Max seconds to wait for CI on HEAD commit |
| `PR_SKIPPED_FILE` | `/srv/stream/merge-queue/prs-skipped.txt` | Skipped PRs (approval, conflict, policy) |
| `PR_MERGED_FILE` | `/srv/stream/merge-queue/prs-merged.txt` | Successfully merged PRs |
| `START_WORKER` | `true` | Set `false` to run worker separately |

## Skip vs failed

| Outcome | File | Examples |
|---------|------|----------|
| **Skipped** | `prs-skipped.txt` | Missing approval, changes requested, merge conflict, branch protection |
| **Failed** | `prs-failed.txt` | CI failed after max reruns, unexpected merge error |
| **Merged** | `prs-merged.txt` | Successfully squash-merged |

View recent outcomes in Slack: `/merge-history` (default last 5).

Re-queue a skipped PR after approval:

```bash
echo 'https://github.com/GetStream/chat/pull/12345' >> /srv/stream/merge-queue/prs.txt
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
