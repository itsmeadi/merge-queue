#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

MERGE_QUEUE_DIR="${MERGE_QUEUE_DIR:-/srv/stream/merge-queue}"
PR_QUEUE_FILE="${PR_QUEUE_FILE:-${MERGE_QUEUE_DIR}/prs.txt}"
PR_FAILED_FILE="${PR_FAILED_FILE:-${MERGE_QUEUE_DIR}/prs-failed.txt}"
PR_SKIPPED_FILE="${PR_SKIPPED_FILE:-${MERGE_QUEUE_DIR}/prs-skipped.txt}"
PR_MERGED_FILE="${PR_MERGED_FILE:-${MERGE_QUEUE_DIR}/prs-merged.txt}"
MAX_RETRIES="${MAX_RETRIES:-5}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
CHECK_INTERVAL="${CHECK_INTERVAL:-10}"
MERGE_METHOD="${MERGE_METHOD:-squash}"
CI_HEAD_WAIT_MAX="${CI_HEAD_WAIT_MAX:-3600}"
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
SLACK_CHANNEL_ID="${SLACK_CHANNEL_ID:-}"
ONCE=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Poll a file of PR URLs, update each with master, wait for CI (rerun on failure),
and merge when green. Optionally post status updates to Slack.

Options:
  -f FILE   Queue file (default: /srv/stream/merge-queue/prs.txt)
  -o FILE   Failed PRs output file (default: /srv/stream/merge-queue/prs-failed.txt)
  -r N      Max CI rerun attempts per PR (default: 5)
  -p N      Poll interval when queue is empty, seconds (default: 10)
  -i N      CI check poll interval, seconds (default: 10)
  -c ID     Slack channel ID for notifications
  --once    Process one PR then exit (even if queue has more)
  -h        Show this help

Environment variables: MERGE_QUEUE_DIR (default: /srv/stream/merge-queue),
PR_QUEUE_FILE, PR_FAILED_FILE, PR_SKIPPED_FILE, PR_MERGED_FILE, MAX_RETRIES,
POLL_INTERVAL, CHECK_INTERVAL, MERGE_METHOD (default: squash),
CI_HEAD_WAIT_MAX, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f) PR_QUEUE_FILE="$2"; shift 2 ;;
    -o) PR_FAILED_FILE="$2"; shift 2 ;;
    -r) MAX_RETRIES="$2"; shift 2 ;;
    -p) POLL_INTERVAL="$2"; shift 2 ;;
    -i) CHECK_INTERVAL="$2"; shift 2 ;;
    -c) SLACK_CHANNEL_ID="$2"; shift 2 ;;
    --once) ONCE=true; shift ;;
    -h) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

die() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed"
}

slack_post() {
  local text="$1"
  if [[ -z "$SLACK_BOT_TOKEN" || -z "$SLACK_CHANNEL_ID" ]]; then
    return 0
  fi
  local payload resp ok
  payload="$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": sys.argv[2]}))' "$SLACK_CHANNEL_ID" "$text")"
  resp="$(curl -sS -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H "Content-type: application/json; charset=utf-8" \
    -d "$payload")"
  ok="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok", False))' <<<"$resp" 2>/dev/null || echo False)"
  if [[ "$ok" != "True" ]]; then
    log "WARN: failed to post to Slack: $resp"
  fi
}

parse_pr_url() {
  local url="$1"
  if [[ "$url" =~ github\.com/([^/]+)/([^/]+)/pull/([0-9]+) ]]; then
    PR_OWNER="${BASH_REMATCH[1]}"
    PR_REPO="${BASH_REMATCH[2]}"
    PR_NUMBER="${BASH_REMATCH[3]}"
    PR_FULL_REPO="${PR_OWNER}/${PR_REPO}"
    return 0
  fi
  return 1
}

read_queue() {
  if [[ ! -f "$PR_QUEUE_FILE" ]]; then
    return 0
  fi
  grep -v '^\s*\(#\|$\)' "$PR_QUEUE_FILE" || true
}

remove_from_queue() {
  local url="$1"
  if [[ ! -f "$PR_QUEUE_FILE" ]]; then
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  grep -Fv "$url" "$PR_QUEUE_FILE" >"$tmp" || true
  mv "$tmp" "$PR_QUEUE_FILE"
}

append_failed() {
  local url="$1"
  local reason="$2"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $url # $reason" >>"$PR_FAILED_FILE"
}

append_skipped() {
  local url="$1"
  local reason="$2"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $url # $reason" >>"$PR_SKIPPED_FILE"
}

append_merged() {
  local url="$1"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $url # merged" >>"$PR_MERGED_FILE"
}

skip_pr() {
  local url="$1"
  local reason="$2"
  log "Skipping $url — $reason"
  append_skipped "$url" "$reason"
  slack_post ":fast_forward: Skipped \`${url}\` — ${reason}"
  remove_from_queue "$url"
}

# Echo skip reason and return 0 if merge is blocked; return 1 if OK to proceed.
merge_blocked_reason() {
  local url="$1"
  local mergeable review merge_state

  mergeable="$(gh pr view "$url" --json mergeable -q .mergeable 2>/dev/null || echo "")"
  review="$(gh pr view "$url" --json reviewDecision -q .reviewDecision 2>/dev/null || echo "")"
  merge_state="$(gh pr view "$url" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null || echo "")"

  if [[ "$mergeable" == "CONFLICTING" ]]; then
    echo "merge conflict"
    return 0
  fi
  case "$review" in
    REVIEW_REQUIRED)
      echo "missing approval"
      return 0
      ;;
    CHANGES_REQUESTED)
      echo "changes requested"
      return 0
      ;;
  esac
  if [[ "$merge_state" == "BLOCKED" && "$mergeable" != "MERGEABLE" && "$merge_state" != "BEHIND" ]]; then
    echo "merge blocked ($merge_state)"
    return 0
  fi
  return 1
}

# Return 0 if error is non-retryable (reviews, branch protection); 1 if unexpected failure.
classify_merge_error() {
  local err="$1"
  grep -qiE 'review|approv|not allowed|blocked|policy|permission|protected|required' <<<"$err"
}

merge_flag() {
  case "$MERGE_METHOD" in
    merge)  echo "--merge" ;;
    squash) echo "--squash" ;;
    rebase) echo "--rebase" ;;
    *) die "Invalid MERGE_METHOD: $MERGE_METHOD (use merge, squash, or rebase)" ;;
  esac
}

merge_state_status() {
  local url="$1"
  gh pr view "$url" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null
}

head_ref_oid() {
  local url="$1"
  gh pr view "$url" --json headRefOid -q .headRefOid 2>/dev/null
}

is_pr_behind() {
  [[ "$(merge_state_status "$1")" == "BEHIND" ]]
}

sync_with_base() {
  local url="$1"

  if ! is_pr_behind "$url"; then
    log "PR branch is up to date with base"
    return 0
  fi

  log "PR is behind base, running update-branch"
  if ! gh pr update-branch "$url"; then
    log "update-branch failed (likely merge conflict)"
    return 1
  fi

  if is_pr_behind "$url"; then
    log "PR still behind base after update-branch"
    return 1
  fi

  log "PR branch synced with base"
  return 0
}

# Wait for CI on the current PR HEAD commit (avoids trusting stale green checks).
# Returns 0 if CI passed, 1 on failure/timeout, 2 if PR became BEHIND during wait.
wait_for_ci_on_head() {
  local url="$1"
  local oid current_oid waited=0 pending failing

  oid="$(head_ref_oid "$url")"
  if [[ -z "$oid" || "$oid" == "null" ]]; then
    log "Could not resolve PR HEAD commit"
    return 1
  fi

  log "Waiting for CI on HEAD ${oid:0:7} (up to ${CI_HEAD_WAIT_MAX}s)"

  while (( waited < CI_HEAD_WAIT_MAX )); do
    if is_pr_behind "$url"; then
      log "PR became behind base while waiting for CI"
      return 2
    fi

    current_oid="$(head_ref_oid "$url")"
    if [[ -n "$current_oid" && "$current_oid" != "$oid" ]]; then
      log "HEAD changed (${oid:0:7} -> ${current_oid:0:7}), waiting for CI on new commit"
      oid="$current_oid"
    fi

    pending="$(gh pr checks "$url" --json bucket -q '[.[] | select(.bucket=="pending")] | length' 2>/dev/null || echo 1)"
    failing="$(gh pr checks "$url" --json bucket -q '[.[] | select(.bucket=="fail")] | length' 2>/dev/null || echo 0)"

    if [[ "$pending" -gt 0 ]]; then
      log "CI pending ($pending check(s)) on HEAD ${oid:0:7}..."
      sleep "$CHECK_INTERVAL"
      waited=$((waited + CHECK_INTERVAL))
      continue
    fi

    if [[ "$failing" -gt 0 ]]; then
      log "CI failed ($failing check(s)) on HEAD ${oid:0:7}"
      return 1
    fi

    if gh pr checks "$url" >/dev/null 2>&1; then
      log "CI passed on HEAD ${oid:0:7}"
      return 0
    fi

    log "Waiting for CI checks on HEAD ${oid:0:7}..."
    sleep "$CHECK_INTERVAL"
    waited=$((waited + CHECK_INTERVAL))
  done

  log "Timeout waiting for CI on HEAD ${oid:0:7}"
  return 1
}

rerun_failed_ci() {
  local url="$1"
  local branch run_id oid
  branch="$(gh pr view "$url" --json headRefName -q .headRefName)"
  oid="$(head_ref_oid "$url")"
  run_id="$(gh run list -R "$PR_FULL_REPO" --commit "$oid" -L 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)"
  if [[ -z "$run_id" || "$run_id" == "null" ]]; then
    run_id="$(gh run list -R "$PR_FULL_REPO" -b "$branch" -L 1 --json databaseId -q '.[0].databaseId')"
  fi
  if [[ -z "$run_id" || "$run_id" == "null" ]]; then
    log "No workflow run found for branch $branch; cannot rerun"
    return 1
  fi
  log "Rerunning failed jobs for run $run_id"
  gh run rerun "$run_id" --failed -R "$PR_FULL_REPO"
}

process_pr() {
  local url="$1"
  local retries=0 ci_result

  log "Processing $url"
  slack_post ":hourglass_flowing_sand: Processing \`${url}\` — syncing with base..."

  if ! parse_pr_url "$url"; then
    log "Malformed URL, moving to failed file: $url"
    append_failed "$url" "malformed URL"
    slack_post ":x: Failed \`${url}\` — malformed URL"
    remove_from_queue "$url"
    return 0
  fi

  local state
  state="$(gh pr view "$url" --json state -q .state 2>/dev/null)" || {
    append_failed "$url" "gh pr view failed"
    slack_post ":x: Failed \`${url}\` — could not fetch PR"
    remove_from_queue "$url"
    return 0
  }

  if [[ "$state" == "MERGED" || "$state" == "CLOSED" ]]; then
    log "PR already $state, removing from queue"
    slack_post ":information_source: \`${url}\` already ${state}, removed from queue"
    remove_from_queue "$url"
    return 0
  fi

  if ! sync_with_base "$url"; then
    skip_pr "$url" "merge conflict (update-branch failed)"
    return 0
  fi

  local skip_reason
  if skip_reason="$(merge_blocked_reason "$url")"; then
    skip_pr "$url" "$skip_reason"
    return 0
  fi

  while true; do
    log "Waiting for CI on current HEAD (attempt $((retries + 1))/$((MAX_RETRIES + 1)))"
    ci_result=0
    wait_for_ci_on_head "$url" || ci_result=$?

    if (( ci_result == 2 )); then
      log "Re-syncing PR with base before retrying CI"
      if ! sync_with_base "$url"; then
        skip_pr "$url" "merge conflict (fell behind during CI)"
        return 0
      fi
      continue
    fi

    if (( ci_result == 0 )); then
      if is_pr_behind "$url"; then
        log "CI passed but PR is behind base — syncing before merge"
        if ! sync_with_base "$url"; then
          skip_pr "$url" "merge conflict (behind base after CI passed)"
          return 0
        fi
        log "Re-running CI after late sync with base"
        continue
      fi

      if skip_reason="$(merge_blocked_reason "$url")"; then
        skip_pr "$url" "$skip_reason"
        return 0
      fi

      log "CI passed and PR is up to date — merging"
      local merge_out=""
      if merge_out="$(gh pr merge "$url" "$(merge_flag)" 2>&1)"; then
        log "Merged $url"
        append_merged "$url"
        slack_post ":white_check_mark: Merged \`${url}\`"
        remove_from_queue "$url"
      elif classify_merge_error "$merge_out"; then
        skip_pr "$url" "$(echo "$merge_out" | head -1)"
      else
        append_failed "$url" "merge failed: $(echo "$merge_out" | head -1)"
        slack_post ":x: Failed \`${url}\` — merge failed after CI passed"
        remove_from_queue "$url"
      fi
      return 0
    fi

    if (( retries >= MAX_RETRIES )); then
      log "CI failed after $MAX_RETRIES reruns, moving to failed file"
      append_failed "$url" "CI failed after $MAX_RETRIES reruns"
      slack_post ":x: Failed \`${url}\` — CI failed after ${MAX_RETRIES} reruns"
      remove_from_queue "$url"
      return 0
    fi

    if is_pr_behind "$url"; then
      log "PR behind base after CI failure — syncing before rerun"
      if ! sync_with_base "$url"; then
        skip_pr "$url" "merge conflict (behind base after CI failure)"
        return 0
      fi
      continue
    fi

    slack_post ":arrows_counterclockwise: \`${url}\` — CI failed, rerunning ($((retries + 1))/${MAX_RETRIES})..."
    if ! rerun_failed_ci "$url"; then
      append_failed "$url" "CI failed and could not rerun workflow"
      slack_post ":x: Failed \`${url}\` — CI failed and could not rerun workflow"
      remove_from_queue "$url"
      return 0
    fi

    retries=$((retries + 1))
  done
}

cleanup() {
  log "Shutting down"
  exit 0
}

main() {
  require_cmd gh
  require_cmd python3

  gh auth status >/dev/null 2>&1 || die "gh is not authenticated; run 'gh auth login'"

  mkdir -p "$(dirname "$PR_QUEUE_FILE")"
  touch "$PR_QUEUE_FILE"
  touch "$PR_FAILED_FILE"
  touch "$PR_SKIPPED_FILE"
  touch "$PR_MERGED_FILE"

  trap cleanup SIGINT SIGTERM

  log "Watching $PR_QUEUE_FILE (poll every ${POLL_INTERVAL}s, max retries: $MAX_RETRIES, merge: $MERGE_METHOD)"
  if [[ -n "$SLACK_BOT_TOKEN" && -n "$SLACK_CHANNEL_ID" ]]; then
    log "Slack notifications enabled (channel: $SLACK_CHANNEL_ID)"
    slack_post ":robot_face: Merge queue worker started (poll every ${POLL_INTERVAL}s)"
  else
    log "WARN: SLACK_BOT_TOKEN or SLACK_CHANNEL_ID unset — worker will not post to Slack"
  fi

  while true; do
    mapfile -t prs < <(read_queue)
    if ((${#prs[@]} == 0)); then
      if $ONCE; then
        exit 0
      fi
      sleep "$POLL_INTERVAL"
      continue
    fi
    process_pr "${prs[0]}"
    if $ONCE; then
      exit 0
    fi
  done
}

main "$@"
