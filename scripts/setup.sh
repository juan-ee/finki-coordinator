#!/usr/bin/env bash
# scripts/setup.sh - one-shot operator bootstrap for the hermes coordinator template.
#
# Pure orchestration (AGENTS.md rule 3): checks env-key presence, delegates config
# validation to 'python -m coordinator.config validate' (T0.4), and writes/installs
# files. No validation logic is reimplemented here. Secret VALUES are never printed -
# key NAMES and set/missing status only (AGENTS.md rule 7); rclone.conf receives the
# values by file write.
#
# Usage: scripts/setup.sh [--dry-run]
#
# Steps:
#   1/5 check required env keys (TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY,
#       GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET, GOOGLE_DRIVE_REFRESH_TOKEN)
#   2/5 validate config/config.yaml via 'python -m coordinator.config validate'
#   3/5 write rclone.conf from env (remote, client id/secret, refresh token, root folder)
#   4/5 install prompts/persona.md -> ${HERMES_HOME:-$HOME/.hermes}/SOUL.md
#   5/5 apply Hermes config: model + cron.model pin + timezone UTC ('hermes config set',
#       with a printed manual fallback when the CLI is absent)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_YAML="$REPO_ROOT/config/config.yaml"
CONFIG_SCHEMA="$REPO_ROOT/config/config.schema.json"
PERSONA_SRC="$REPO_ROOT/prompts/persona.md"
RCLONE_CONF_PATH="${RCLONE_CONFIG_PATH:-$HOME/.config/rclone/rclone.conf}"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
HERMES_MODEL_VALUE="${HERMES_MODEL:-nousresearch/hermes-4-70b}"
RCLONE_REMOTE_VALUE="${RCLONE_REMOTE:-shareddrive:}"

REQUIRED_KEYS=(
  TELEGRAM_BOT_TOKEN
  OPENROUTER_API_KEY
  GOOGLE_DRIVE_CLIENT_ID
  GOOGLE_DRIVE_CLIENT_SECRET
  GOOGLE_DRIVE_REFRESH_TOKEN
)

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *)
      echo "usage: scripts/setup.sh [--dry-run]" >&2
      exit 2
      ;;
  esac
done

step_env_check() {
  echo "== [1/5] Required env keys (names only; values are never printed) =="
  local missing=() key
  for key in "${REQUIRED_KEYS[@]}"; do
    if [[ -n "${!key:-}" ]]; then
      echo "  set:     $key"
    else
      echo "  missing: $key"
      missing+=("$key")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    return 0
  fi
  echo "ERROR: missing required env keys: ${missing[*]}" >&2
  echo "       export them in your shell (or put them in .env, loaded by docker compose)" >&2
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "       dry-run: continuing despite missing keys" >&2
    return 0
  fi
  exit 1
}

step_validate_config() {
  echo "== [2/5] Validate config/config.yaml (python -m coordinator.config validate) =="
  if [[ ! -f "$CONFIG_YAML" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "  WARNING: $CONFIG_YAML not found (the repo ships config/config.example.yaml only)"
      echo "  dry-run continues; real mode would exit 1 until the file is created"
      return 0
    fi
    echo "ERROR: $CONFIG_YAML not found." >&2
    echo "       Copy config/config.example.yaml to config/config.yaml and edit it, then re-run." >&2
    exit 1
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD run: python -m coordinator.config validate config/config.yaml config/config.schema.json"
    return 0
  fi
  (cd "$REPO_ROOT" && python -m coordinator.config validate config/config.yaml config/config.schema.json)
}

step_write_rclone_conf() {
  echo "== [3/5] rclone.conf (values go into the file, never onto stdout) =="
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD write: $RCLONE_CONF_PATH"
    echo "  stanza shape (env-derived values redacted):"
    echo "    [$RCLONE_REMOTE_VALUE]"
    echo "    type = drive"
    echo "    client_id = <redacted:GOOGLE_DRIVE_CLIENT_ID>"
    echo "    client_secret = <redacted:GOOGLE_DRIVE_CLIENT_SECRET>"
    echo "    token = <redacted:GOOGLE_DRIVE_REFRESH_TOKEN (embedded as JSON)>"
    if [[ -n "${RCLONE_ROOT_FOLDER_ID:-}" ]]; then
      echo "    root_folder_id = <redacted:RCLONE_ROOT_FOLDER_ID>"
    else
      echo "    root_folder_id = <empty>"
    fi
    return 0
  fi
  mkdir -p "$(dirname "$RCLONE_CONF_PATH")"
  (
    umask 077
    cat > "$RCLONE_CONF_PATH" <<RCLONE_EOF
[$RCLONE_REMOTE_VALUE]
type = drive
client_id = $GOOGLE_DRIVE_CLIENT_ID
client_secret = $GOOGLE_DRIVE_CLIENT_SECRET
token = {"access_token":"","token_type":"bearer","refresh_token":"$GOOGLE_DRIVE_REFRESH_TOKEN"}
root_folder_id = ${RCLONE_ROOT_FOLDER_ID:-}
RCLONE_EOF
  )
  chmod 600 "$RCLONE_CONF_PATH"
  echo "  wrote: $RCLONE_CONF_PATH (0600)"
}

step_install_soul_md() {
  echo "== [4/5] SOUL.md (persona) =="
  if [[ "$DRY_RUN" -eq 1 ]]; then
    if [[ -f "$PERSONA_SRC" ]]; then
      echo "  WOULD install: $PERSONA_SRC -> $HERMES_HOME_DIR/SOUL.md"
    else
      echo "  WARNING: $PERSONA_SRC not found (the persona ships in phase 1, T1.1)"
      echo "  WOULD install it to: $HERMES_HOME_DIR/SOUL.md (dry-run continues)"
    fi
    return 0
  fi
  if [[ ! -f "$PERSONA_SRC" ]]; then
    echo "WARNING: $PERSONA_SRC not found - the persona ships in phase 1 (T1.1)." >&2
    echo "         Skipping the SOUL.md install; re-run setup after adding it." >&2
    return 0
  fi
  mkdir -p "$HERMES_HOME_DIR"
  cp "$PERSONA_SRC" "$HERMES_HOME_DIR/SOUL.md"
  echo "  installed: $HERMES_HOME_DIR/SOUL.md"
}

hermes_set() {
  echo "  running: hermes config set $1 $2"
  hermes config set "$1" "$2"
}

step_apply_hermes_config() {
  echo "== [5/5] Hermes config (model + cron.model pin + timezone UTC) =="
  local model="$HERMES_MODEL_VALUE"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD run (via the hermes CLI if present; otherwise apply manually):"
    echo "    hermes config set model $model"
    echo "    hermes config set cron.model $model"
    echo "    hermes config set timezone UTC"
    return 0
  fi
  if ! command -v hermes >/dev/null 2>&1; then
    echo "  hermes CLI not found on PATH; apply these manually once hermes is installed:"
    echo "    hermes config set model $model"
    echo "    hermes config set cron.model $model"
    echo "    hermes config set timezone UTC"
    return 0
  fi
  hermes_set model "$model"
  hermes_set cron.model "$model"
  hermes_set timezone UTC
}

step_next_steps() {
  echo "== Next steps =="
  cat <<'NEXT_EOF'
  1. docker compose up -d        # compose template lands in T0.14
  2. python scripts/init_db.py   # initialize the SQLite database (scripts/init_db.py)
  3. OpenRouter: set a monthly credit limit (~$20) on your key at https://openrouter.ai/keys
NEXT_EOF
}

main() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "hermes-setup: DRY RUN - printing actions, writing nothing"
  else
    echo "hermes-setup: applying configuration"
  fi
  step_env_check
  step_validate_config
  step_write_rclone_conf
  step_install_soul_md
  step_apply_hermes_config
  step_next_steps
  echo "done."
}

main "$@"
