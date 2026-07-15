#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

MERGE_QUEUE_DIR="${MERGE_QUEUE_DIR:-$SCRIPT_DIR}"
PR_QUEUE_FILE="${PR_QUEUE_FILE:-${MERGE_QUEUE_DIR}/prs.txt}"
PR_FAILED_FILE="${PR_FAILED_FILE:-${MERGE_QUEUE_DIR}/prs-failed.txt}"
PR_SKIPPED_FILE="${PR_SKIPPED_FILE:-${MERGE_QUEUE_DIR}/prs-skipped.txt}"
PR_MERGED_FILE="${PR_MERGED_FILE:-${MERGE_QUEUE_DIR}/prs-merged.txt}"
PR_THREADS_FILE="${PR_THREADS_FILE:-${MERGE_QUEUE_DIR}/prs-threads.json}"
MAX_RETRIES="${MAX_RETRIES:-3}"
POLL_INTERVAL="${POLL_INTERVAL:-10}"
CHECK_INTERVAL="${CHECK_INTERVAL:-10}"
MERGE_METHOD="${MERGE_METHOD:-squash}"
CI_HEAD_WAIT_MAX="${CI_HEAD_WAIT_MAX:-3600}"
CI_SETTLE_AFTER_SYNC="${CI_SETTLE_AFTER_SYNC:-45}"
REQUIRED_CHECK="${REQUIRED_CHECK:-Ready to merge}"
PR_PROCESSING_FILE="${PR_PROCESSING_FILE:-${MERGE_QUEUE_DIR}/processing.txt}"
QUEUE_STATUS_FILE="${QUEUE_STATUS_FILE:-${MERGE_QUEUE_DIR}/queue-status.json}"
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
SLACK_CHANNEL_ID="${SLACK_CHANNEL_ID:-}"
MERGED_REACTION_EMOJI="${MERGED_REACTION_EMOJI:-merged}"
ONCE=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Poll a file of PR URLs, update each with master, wait for CI (rerun on failure),
and merge when green. Optionally post status updates to Slack.

Options:
  -f FILE   Queue file (default: \$MERGE_QUEUE_DIR/prs.txt, install dir)
  -o FILE   Failed PRs output file (default: \$MERGE_QUEUE_DIR/prs-failed.txt)
  -r N      Max CI rerun attempts per PR (default: 3)
  -p N      Poll interval when queue is empty, seconds (default: 10)
  -i N      CI check poll interval, seconds (default: 10)
  -c ID     Slack channel ID for notifications
  --once    Process one PR then exit (even if queue has more)
  -h        Show this help

Environment variables: MERGE_QUEUE_DIR (default: install dir, same as this script),
PR_QUEUE_FILE, PR_FAILED_FILE, PR_SKIPPED_FILE, PR_MERGED_FILE, MAX_RETRIES,
POLL_INTERVAL, CHECK_INTERVAL, MERGE_METHOD (default: squash),
CI_HEAD_WAIT_MAX, CI_SETTLE_AFTER_SYNC, REQUIRED_CHECK, PR_PROCESSING_FILE,
SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, MERGED_REACTION_EMOJI
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
  local channel="${2:-$SLACK_CHANNEL_ID}"
  local thread_ts="${3:-}"
  if [[ -z "$SLACK_BOT_TOKEN" || -z "$channel" ]]; then
    return 0
  fi
  local payload resp ok
  if [[ -n "$thread_ts" ]]; then
    payload="$(python3 -c 'import json,sys; d={"channel": sys.argv[1], "text": sys.argv[2], "thread_ts": sys.argv[3]}; print(json.dumps(d))' "$channel" "$text" "$thread_ts")"
  else
    payload="$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "text": sys.argv[2]}))' "$channel" "$text")"
  fi
  resp="$(curl -sS -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H "Content-type: application/json; charset=utf-8" \
    -d "$payload")"
  ok="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok", False))' <<<"$resp" 2>/dev/null || echo False)"
  if [[ "$ok" != "True" ]]; then
    log "WARN: failed to post to Slack: $resp"
  fi
}

slack_react() {
  local channel="$1"
  local ts="$2"
  local emoji="$3"
  if [[ -z "$SLACK_BOT_TOKEN" || -z "$channel" || -z "$ts" || -z "$emoji" ]]; then
    return 0
  fi
  local payload resp ok
  payload="$(python3 -c 'import json,sys; print(json.dumps({"channel": sys.argv[1], "timestamp": sys.argv[2], "name": sys.argv[3]}))' "$channel" "$ts" "$emoji")"
  resp="$(curl -sS -X POST https://slack.com/api/reactions.add \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H "Content-type: application/json; charset=utf-8" \
    -d "$payload")"
  ok="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("ok", False))' <<<"$resp" 2>/dev/null || echo False)"
  if [[ "$ok" != "True" ]]; then
    log "WARN: failed to add reaction ${emoji}: $resp"
  fi
}

slack_react_for_pr() {
  local url="$1"
  local emoji="${2:-$MERGED_REACTION_EMOJI}"
  local channel thread_ts
  read -r channel thread_ts < <(lookup_thread "$url")
  if [[ -n "$channel" && -n "$thread_ts" ]]; then
    slack_react "$channel" "$thread_ts" "$emoji"
  fi
}

lookup_thread() {
  local url="$1"
  python3 "$SCRIPT_DIR/queue_meta.py" lookup "$PR_THREADS_FILE" "$url" 2>/dev/null || true
}

clear_thread_meta() {
  local url="$1"
  python3 "$SCRIPT_DIR/queue_meta.py" clear "$PR_THREADS_FILE" "$url" >/dev/null 2>&1 || true
}

slack_post_for_pr() {
  local text="$1"
  local url="$2"
  local channel thread_ts
  read -r channel thread_ts < <(lookup_thread "$url")
  if [[ -n "$channel" && -n "$thread_ts" ]]; then
    slack_post "$text" "$channel" "$thread_ts"
  else
    slack_post "$text"
  fi
}

notify_requester() {
  local url="$1"
  local text="$2"
  if [[ -z "$SLACK_BOT_TOKEN" ]]; then
    return 0
  fi
  MERGE_NOTIFY_TEXT="$text" python3 "$SCRIPT_DIR/slack_notify.py" dm-for-pr "$url" >/dev/null 2>&1 || true
}

refresh_queue_status() {
  local finished_url="${1:-}"
  local finished_label="${2:-done}"
  if [[ -z "$SLACK_BOT_TOKEN" || -z "$SLACK_CHANNEL_ID" ]]; then
    return 0
  fi
  python3 "$SCRIPT_DIR/slack_status.py" refresh "$finished_url" "$finished_label" >/dev/null 2>&1 || true
}

collect_ci_failure() {
  local url="$1"
  python3 "$SCRIPT_DIR/ci_summary.py" "$url" 2>/dev/null || echo '{"failed_checks":[],"job":"","excerpt":""}'
}

slack_msg_with_summary() {
  local cmd="$1"
  local summary="$2"
  shift 2
  if [[ -n "$summary" && "$summary" != "{}" ]]; then
    printf '%s' "$summary" | python3 "$SCRIPT_DIR/messages.py" "$cmd" "$@" --summary-stdin \
      || python3 "$SCRIPT_DIR/messages.py" "$cmd" "$@" \
      || true
  else
    slack_msg "$cmd" "$@"
  fi
}

slack_msg() {
  python3 "$SCRIPT_DIR/messages.py" "$@" || true
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
  clear_thread_meta "$url"
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
  if [[ -f "$PR_MERGED_FILE" ]] && grep -qF "$url # merged" "$PR_MERGED_FILE"; then
    log "Already recorded merged: $url"
    return 0
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') $url # merged" >>"$PR_MERGED_FILE"
}

record_merged_pr() {
  local url="$1"
  local msg
  append_merged "$url"
  slack_react_for_pr "$url"
  msg="$(slack_msg merged "$url")"
  slack_post "$msg"
  notify_requester "$url" "$msg"
  remove_from_queue "$url"
  refresh_queue_status "$url" "merged"
}

skip_pr() {
  local url="$1"
  local reason="$2"
  local msg
  log "Skipping $url — $reason"
  append_skipped "$url" "$reason"
  msg="$(slack_msg skip "$url" "$reason")"
  slack_post "$msg"
  notify_requester "$url" "$msg"
  remove_from_queue "$url"
  refresh_queue_status "$url" "skipped"
}

finish_failed() {
  local url="$1"
  local reason="$2"
  local msg
  append_failed "$url" "$reason"
  msg="$(slack_msg failed "$url" "$reason")"
  slack_post "$msg"
  notify_requester "$url" "$msg"
  remove_from_queue "$url"
  refresh_queue_status "$url" "failed"
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
  return 1
}

# Skip reasons that should abort before waiting for CI (reviews, hard conflicts).
preflight_blocked_reason() {
  merge_blocked_reason "$@"
}

# Block merge attempts while GitHub still reports BLOCKED (pending required checks, etc.).
merge_attempt_blocked_reason() {
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
  if [[ "$merge_state" == "BEHIND" ]]; then
    echo "behind base"
    return 0
  fi
  if [[ "$merge_state" == "BLOCKED" ]]; then
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

is_already_merged_error() {
  local err="$1"
  grep -qiE 'already merged|was merged|not open|pull request is closed' <<<"$err"
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
  gh pr view "$url" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null || true
}

head_ref_oid() {
  local url="$1"
  gh pr view "$url" --json headRefOid -q .headRefOid 2>/dev/null || true
}

is_pr_behind() {
  [[ "$(merge_state_status "$1")" == "BEHIND" ]]
}

required_check_bucket() {
  local url="$1"
  gh pr checks "$url" --json name,bucket -q \
    '.[] | select(.name=="'"$REQUIRED_CHECK"'") | .bucket' 2>/dev/null | head -1 || true
}

set_processing() {
  echo "$1" >"$PR_PROCESSING_FILE"
}

clear_processing() {
  rm -f "$PR_PROCESSING_FILE"
}

sync_with_base() {
  local url="$1"
  local updated=false

  if ! is_pr_behind "$url"; then
    log "PR branch is up to date with base"
    return 0
  fi

  log "PR is behind base, running update-branch"
  if ! gh pr update-branch "$url"; then
    log "update-branch failed (likely merge conflict)"
    return 1
  fi
  updated=true

  if is_pr_behind "$url"; then
    log "PR still behind base after update-branch"
    return 1
  fi

  if $updated; then
    log "Waiting ${CI_SETTLE_AFTER_SYNC}s for CI to restart after update-branch"
    sleep "$CI_SETTLE_AFTER_SYNC"
  fi

  log "PR branch synced with base"
  return 0
}

# Wait for CI on the current PR HEAD commit (avoids trusting stale green checks).
# Returns 0 if CI passed, 1 on failure/timeout, 2 if PR became BEHIND during wait.
wait_for_ci_on_head() {
  local url="$1"
  local oid current_oid waited=0 pending failing required

  oid="$(head_ref_oid "$url")"
  if [[ -z "$oid" || "$oid" == "null" ]]; then
    log "Could not resolve PR HEAD commit"
    return 1
  fi

  log "Waiting for CI on HEAD ${oid:0:7} (required check: ${REQUIRED_CHECK}, up to ${CI_HEAD_WAIT_MAX}s)"

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
    required="$(required_check_bucket "$url")"

    if [[ "$pending" -gt 0 ]]; then
      log "CI pending ($pending check(s)) on HEAD ${oid:0:7}..."
      sleep "$CHECK_INTERVAL"
      waited=$((waited + CHECK_INTERVAL))
      continue
    fi

    if [[ -z "$required" || "$required" == "pending" ]]; then
      log "Waiting for required check '${REQUIRED_CHECK}' on HEAD ${oid:0:7}..."
      sleep "$CHECK_INTERVAL"
      waited=$((waited + CHECK_INTERVAL))
      continue
    fi

    if [[ "$required" == "fail" || "$failing" -gt 0 ]]; then
      log "CI failed on HEAD ${oid:0:7} (${REQUIRED_CHECK}=${required}, failing=${failing})"
      return 1
    fi

    if [[ "$required" == "pass" ]]; then
      log "CI passed on HEAD ${oid:0:7} (${REQUIRED_CHECK} green)"
      return 0
    fi

    log "Waiting for '${REQUIRED_CHECK}' (bucket=${required}) on HEAD ${oid:0:7}..."
    sleep "$CHECK_INTERVAL"
    waited=$((waited + CHECK_INTERVAL))
  done

  log "Timeout waiting for CI on HEAD ${oid:0:7}"
  return 1
}

rerun_failed_ci() {
  local url="$1"
  local run_id

  run_id="$(python3 "$SCRIPT_DIR/ci_summary.py" rerun-run-id "$url" 2>/dev/null || true)"
  if [[ -z "$run_id" ]]; then
    log "No failed Actions run to rerun (failures may be aggregate checks like '${REQUIRED_CHECK}')"
    return 1
  fi

  log "Rerunning failed jobs for run $run_id"
  if ! gh run rerun "$run_id" --failed -R "$PR_FULL_REPO"; then
    log "gh run rerun failed for run $run_id"
    return 1
  fi
  return 0
}

process_pr() {
  local url="$1"
  local retries=0 ci_result msg

  log "Processing $url"
  set_processing "$url"
  trap 'clear_processing' RETURN
  refresh_queue_status
  slack_post "$(slack_msg processing "$url")"

  if ! parse_pr_url "$url"; then
    log "Malformed URL, moving to failed file: $url"
    finish_failed "$url" "malformed URL"
    return 0
  fi

  local state
  state="$(gh pr view "$url" --json state -q .state 2>/dev/null)" || {
    finish_failed "$url" "gh pr view failed"
    return 0
  }

  if [[ "$state" == "MERGED" || "$state" == "CLOSED" ]]; then
    log "PR already $state, removing from queue"
    msg="$(slack_msg already_done "$url" "$state")"
    slack_post "$msg"
    notify_requester "$url" "$msg"
    remove_from_queue "$url"
    if [[ "$state" == "MERGED" ]]; then
      refresh_queue_status "$url" "merged"
    else
      refresh_queue_status "$url" "closed"
    fi
    return 0
  fi

  if ! sync_with_base "$url"; then
    skip_pr "$url" "merge conflict (update-branch failed)"
    return 0
  fi

  local skip_reason
  if skip_reason="$(preflight_blocked_reason "$url")"; then
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

      if skip_reason="$(merge_attempt_blocked_reason "$url")"; then
        skip_pr "$url" "$skip_reason"
        return 0
      fi

      if [[ "$(required_check_bucket "$url")" != "pass" ]]; then
        log "Required check '${REQUIRED_CHECK}' not green after CI wait — retrying"
        continue
      fi

      log "CI passed and PR is up to date — merging"
      state="$(gh pr view "$url" --json state -q .state 2>/dev/null || echo "")"
      if [[ "$state" == "MERGED" ]]; then
        log "PR already merged (likely auto-merge) — recording outcome"
        record_merged_pr "$url"
        return 0
      fi

      local merge_out=""
      if merge_out="$(gh pr merge "$url" "$(merge_flag)" 2>&1)"; then
        log "Merged $url"
        record_merged_pr "$url"
      elif is_already_merged_error "$merge_out"; then
        log "PR already merged during merge attempt — recording outcome"
        record_merged_pr "$url"
      elif classify_merge_error "$merge_out"; then
        skip_pr "$url" "$(echo "$merge_out" | head -1)"
      else
        finish_failed "$url" "merge failed after CI passed"
      fi
      return 0
    fi

    # CI failed (ci_result != 0). Disable set -e for this path so Slack/gh
    # noise cannot abort the worker into a restart loop.
    set +e
    if (( retries >= MAX_RETRIES )); then
      log "CI failed after $MAX_RETRIES reruns, moving to failed file"
      summary="$(collect_ci_failure "$url")"
      msg="$(slack_msg_with_summary ci_failed "$summary" "$url" "$MAX_RETRIES")"
      if [[ -n "$msg" ]]; then
        slack_post_for_pr "$msg" "$url"
        notify_requester "$url" "$msg"
      fi
      append_failed "$url" "CI failed after $MAX_RETRIES reruns"
      remove_from_queue "$url"
      refresh_queue_status "$url" "failed"
      set -e
      return 0
    fi

    if is_pr_behind "$url"; then
      log "PR behind base after CI failure — syncing before rerun"
      if ! sync_with_base "$url"; then
        set -e
        skip_pr "$url" "merge conflict (behind base after CI failure)"
        return 0
      fi
      set -e
      continue
    fi

    log "CI failed — collecting summary and attempting rerun (retry $((retries + 1))/$MAX_RETRIES)"
    summary="$(collect_ci_failure "$url")"
    msg="$(slack_msg_with_summary ci_rerun "$summary" "$url" "$((retries + 1))" "$MAX_RETRIES")"
    if [[ -n "$msg" ]]; then
      slack_post_for_pr "$msg" "$url"
    fi
    if ! rerun_failed_ci "$url"; then
      set -e
      finish_failed "$url" "CI failed and could not rerun workflow"
      return 0
    fi

    retries=$((retries + 1))
    set -e
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
    slack_post "$(slack_msg worker_started "$POLL_INTERVAL")"
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
    # Isolate process_pr so set -e inside cannot kill the whole worker loop.
    set +e
    process_pr "${prs[0]}"
    pr_rc=$?
    set -e
    if (( pr_rc != 0 )); then
      log "WARN: process_pr exited $pr_rc for ${prs[0]} — continuing queue loop"
    fi
    if $ONCE; then
      exit 0
    fi
  done
}

main "$@"
