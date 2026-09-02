#!/usr/bin/env bash
# scripts/sync.sh - rclone bisync wrapper: local mirror (data/project/) <-> Shared Drive.
#
# Pure orchestration (AGENTS.md rule 3): validates flags/env, composes a single
# `rclone bisync` command, and appends its own plus rclone's output to
# data/logs/sync.log. No business logic, no timezone math (log stamps are plain UTC
# wall-clock for humans). Callers: the digest skill's on-demand beat and the
# container boot (proposal §3).
#
# Guardrail (proposal §3): --resync is the only way bisync eats files, so it is
# refused without --i-know-what-im-doing. The recovery runbook lands with T4.2.
#
# Usage:
#   scripts/sync.sh [--dry-run] [--boot]
#   scripts/sync.sh --resync --i-know-what-im-doing [--dry-run]
#
# Flags:
#   --dry-run                 print the rclone command that would run; write nothing
#   --boot                    tag the run as a container-boot sync (console/log marker)
#   --resync                  one-time bisync baseline (re)initialization - destructive
#   --i-know-what-im-doing    acknowledgement required by --resync
#
# Environment (see .env.example):
#   RCLONE_REMOTE           required: rclone remote NAME, e.g. "shareddrive:"
#   RCLONE_ROOT_FOLDER_ID   optional: pin sync to a specific Drive folder id
#                           (non-secret per proposal §2; printed in --dry-run only,
#                           never written to the log)
#
# Log: data/logs/sync.log - created/appended at runtime only (data/ is gitignored).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_ROOT="$REPO_ROOT/data/project"
LOG_FILE="$REPO_ROOT/data/logs/sync.log"

DRY_RUN=0
BOOT_MODE=0
RESYNC=0
ACK=0

usage() {
  cat <<'USAGE_EOF'
usage: scripts/sync.sh [--dry-run] [--boot] [--resync --i-know-what-im-doing]

  --dry-run                 print the rclone command that would run; write nothing
  --boot                    tag the run as a container-boot sync (log marker)
  --resync                  one-time bisync baseline (re)initialization (destructive)
  --i-know-what-im-doing    required acknowledgement for --resync

environment (see .env.example):
  RCLONE_REMOTE             required: rclone remote NAME, e.g. "shareddrive:"
  RCLONE_ROOT_FOLDER_ID     optional: pin sync to a specific Drive folder id

log: data/logs/sync.log
USAGE_EOF
}

die() {
  echo "ERROR: $*" >&2
  usage >&2
  exit 2
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --boot) BOOT_MODE=1 ;;
    --resync) RESYNC=1 ;;
    --i-know-what-im-doing) ACK=1 ;;
    *) die "unknown flag: $arg" ;;
  esac
done

# ── Validation ───────────────────────────────────────────────────────────────────────
# Everything up to and including the dry-run print must stay side-effect free (no
# mkdir, no log append): every die() path is exercised by tests that must not write
# into the repo tree.

if [[ -z "${RCLONE_REMOTE:-}" ]]; then
  die "RCLONE_REMOTE is not set. Set it in .env (see .env.example), e.g. RCLONE_REMOTE=shareddrive:, and re-run."
fi

if [[ "$RESYNC" -eq 1 && "$ACK" -eq 0 ]]; then
  die "--resync refused without --i-know-what-im-doing. A blind resync can permanently overwrite either side of the mirror; read the bisync recovery runbook, then re-run with both flags if you are sure."
fi

if [[ "$DRY_RUN" -eq 0 ]] && ! command -v rclone >/dev/null 2>&1; then
  die "rclone is not installed or not on PATH. Install it (https://rclone.org/install/) and run 'rclone config' so the remote named in RCLONE_REMOTE exists."
fi

if [[ "$DRY_RUN" -eq 0 && "$RESYNC" -eq 0 && ! -d "$LOCAL_ROOT" ]]; then
  die "local mirror $LOCAL_ROOT does not exist yet. Bootstrap it once with: scripts/sync.sh --resync --i-know-what-im-doing"
fi

# ── Compose the command ──────────────────────────────────────────────────────────────

RCLONE_ARGS=(bisync -v)
if [[ "$RESYNC" -eq 1 ]]; then
  RCLONE_ARGS+=(--resync)
fi
if [[ -n "${RCLONE_ROOT_FOLDER_ID:-}" ]]; then
  RCLONE_ARGS+=(--drive-root-folder-id "$RCLONE_ROOT_FOLDER_ID")
fi
RCLONE_ARGS+=("$RCLONE_REMOTE" "$LOCAL_ROOT")

RUN_KIND="scheduled"
LOG_PREFIX=""
if [[ "$BOOT_MODE" -eq 1 ]]; then
  RUN_KIND="boot"
  LOG_PREFIX="[boot] "
fi

# ── Dry run: print the plan, touch nothing ───────────────────────────────────────────

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "sync: DRY RUN (mode: $RUN_KIND) - would run, writing nothing:"
  printf '  rclone'
  printf ' %q' "${RCLONE_ARGS[@]}"
  echo
  echo "  log target: $LOG_FILE"
  if [[ ! -d "$LOCAL_ROOT" ]]; then
    echo "  note: $LOCAL_ROOT does not exist yet (bootstrap once with --resync --i-know-what-im-doing after .env is set)"
  fi
  exit 0
fi

# ── Real run: tee output to data/logs/sync.log, propagate rclone's exit code ─────────

if [[ "$RESYNC" -eq 1 ]]; then
  mkdir -p "$LOCAL_ROOT" # first bootstrap: bisync initializes into the (empty) dir
fi
mkdir -p "$(dirname "$LOG_FILE")"

{
  echo ""
  echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ${LOG_PREFIX}bisync start (remote=$RCLONE_REMOTE resync=$RESYNC) ===="
} >>"$LOG_FILE"

rc=0
rclone "${RCLONE_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE" || rc=$?
echo "===== $(date -u '+%Y-%m-%dT%H:%M:%SZ') ${LOG_PREFIX}bisync end rc=$rc =====" >>"$LOG_FILE"

if [[ "$rc" -ne 0 ]]; then
  echo "ERROR: bisync failed (rc=$rc) - full output: $LOG_FILE" >&2
  exit "$rc"
fi
echo "sync OK ($RUN_KIND run) - log: $LOG_FILE"
