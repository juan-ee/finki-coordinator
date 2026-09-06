#!/usr/bin/env bash
# scripts/setup.sh - one-shot operator bootstrap for the hermes coordinator template.
#
# Pure orchestration (AGENTS.md rule 3): checks env-key presence, delegates config
# validation to 'python -m coordinator.config validate' (T0.4), and writes/installs
# files. No validation logic is reimplemented here. Secret VALUES are never printed -
# key NAMES and set/missing status only (AGENTS.md rule 7).
#
# Usage: scripts/setup.sh [--dry-run]
#
# Steps:
#   1/7 check required env keys (TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY)
#   2/7 validate config/config.yaml via 'python -m coordinator.config validate'
#   3/7 install prompts/persona.md -> ${HERMES_HOME:-$HOME/.hermes}/SOUL.md
#   4/7 install prompts/skills/*/SKILL.md -> ${HERMES_HOME:-$HOME/.hermes}/skills/
#       coordinator-<name>/SKILL.md (template-owned: overwrites on every run, same
#       precedent as the SOUL.md install; no rebuild/restart needed — the runtime
#       skill store lives on the ~/.hermes volume)
#   5/7 apply Hermes config: model + cron.model pin + timezone UTC ('hermes config set',
#       with a printed manual fallback when the CLI is absent)
#   6/7 enable the coordinator plugin + kanban toolset (plugins.enabled / top-level
#       toolsets — upstream opt-in gates; printed in-container fallback when the CLI
#       is absent, since the plugin dir only materializes inside the container)
#   7/7 seed data/project/ from project-template/ (first-boot files; existing files
#       are never overwritten)
#
# Overridable env knobs (defaults keep production behavior; overrides exist for
# testability and relocated installs): HERMES_HOME, HERMES_MODEL,
# PROJECT_DATA_ROOT (seed target root, default $REPO_ROOT/data), CONFIG_YAML and
# CONFIG_SCHEMA (validation inputs).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_YAML="${CONFIG_YAML:-$REPO_ROOT/config/config.yaml}"
CONFIG_SCHEMA="${CONFIG_SCHEMA:-$REPO_ROOT/config/config.schema.json}"
PROJECT_DATA_ROOT="${PROJECT_DATA_ROOT:-$REPO_ROOT/data}"
PERSONA_SRC="$REPO_ROOT/prompts/persona.md"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
HERMES_MODEL_VALUE="${HERMES_MODEL:-nousresearch/hermes-4-70b}"

REQUIRED_KEYS=(
  TELEGRAM_BOT_TOKEN
  OPENROUTER_API_KEY
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
  echo "== [1/6] Required env keys (names only; values are never printed) =="
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
  echo "== [2/6] Validate config/config.yaml (python -m coordinator.config validate) =="
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
    echo "  WOULD run: python -m coordinator.config validate '$CONFIG_YAML' '$CONFIG_SCHEMA'"
    return 0
  fi
  (cd "$REPO_ROOT" && python -m coordinator.config validate "$CONFIG_YAML" "$CONFIG_SCHEMA")
}

step_install_soul_md() {
  echo "== [3/7] SOUL.md (persona) =="
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

step_install_skills() {
  echo "== [4/7] Coordinator skills -> runtime skill store (template-owned, overwrite) =="
  local src name dest installed=0
  for src in "$REPO_ROOT"/prompts/skills/*/SKILL.md; do
    if [[ ! -f "$src" ]]; then
      echo "  WARNING: no prompts/skills/*/SKILL.md found - skipping the skills install." >&2
      echo "           Re-run setup after adding them." >&2
      return 0
    fi
    name="$(basename "$(dirname "$src")")"
    dest="$HERMES_HOME_DIR/skills/coordinator-$name/SKILL.md"
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "  WOULD install: $src -> $dest"
    else
      mkdir -p "$(dirname "$dest")"
      cp "$src" "$dest"
      echo "  installed: $dest"
      installed=$((installed + 1))
    fi
  done
  if [[ "$DRY_RUN" -ne 1 ]]; then
    echo "  installed $installed skill(s) under $HERMES_HOME_DIR/skills/"
  fi
}

hermes_set() {
  echo "  running: hermes config set $1 $2"
  hermes config set "$1" "$2"
}

step_apply_hermes_config() {
  echo "== [5/7] Hermes config (model + cron.model pin + timezone UTC) =="
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

print_in_container_fallback() {
  # One shared block: the exact commands when the hermes CLI is absent (values quoted
  # so a copy-pasted line survives bash globbing).
  echo "    docker compose exec gateway hermes config set plugins.enabled '[\"coordinator\"]'"
  echo "    docker compose exec gateway hermes config set toolsets '[\"hermes-cli\", \"kanban\"]'"
  echo "    docker compose restart gateway   # only if the gateway was already running"
}

step_enable_plugin_toolsets() {
  echo "== [6/7] Coordinator plugin + kanban toolset (runtime config) =="
  # Upstream gates user plugins behind config.yaml plugins.enabled (opt-in), and the
  # kanban tools' check_fn reads the top-level toolsets list (the all wildcard does NOT
  # enable kanban). Written via 'hermes config set' — a pure config write, so it needs
  # no on-disk plugin discovery (the plugin dir only materializes inside the container).
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD run (via the hermes CLI if present; otherwise apply after boot):"
    echo "    hermes config set plugins.enabled '[\"coordinator\"]'"
    echo "    hermes config set toolsets '[\"hermes-cli\", \"kanban\"]'"
    echo "  If the CLI is absent, run inside the container after 'docker compose up -d':"
    print_in_container_fallback
    return 0
  fi
  if ! command -v hermes >/dev/null 2>&1; then
    echo "  hermes CLI not found on PATH; apply inside the container after 'docker compose up -d':"
    print_in_container_fallback
    return 0
  fi
  hermes_set "plugins.enabled" '["coordinator"]'
  hermes_set "toolsets" '["hermes-cli", "kanban"]'
}

step_seed_project_template() {
  echo "== [7/7] Seed data/project/ from project-template/ (existing files never overwritten) =="
  local template_dir="$REPO_ROOT/project-template"
  local target_root="$PROJECT_DATA_ROOT/project"
  if [[ ! -d "$template_dir" ]]; then
    echo "  WARNING: $template_dir not found - skipping the project seed" >&2
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD seed: $target_root (first boot; existing files are never overwritten)"
    local rel
    while IFS= read -r rel; do
      rel="${rel#"$template_dir"/}"
      if [[ -e "$target_root/$rel" ]]; then
        echo "  exists (keep): $rel"
      else
        echo "  WOULD copy: $rel"
      fi
    done < <(find "$template_dir" -type f | sort)
    return 0
  fi
  # First-boot seed: copy each template file only if the target does not exist yet.
  # Re-runs and relocated DATA roots never clobber operator content (T2.3 guarantee).
  mkdir -p "$target_root"
  local copied=0 kept=0 src rel
  while IFS= read -r src; do
    rel="${src#"$template_dir"/}"
    mkdir -p "$target_root/$(dirname "$rel")"
    if [[ -e "$target_root/$rel" ]]; then
      kept=$((kept + 1))
    else
      cp "$src" "$target_root/$rel"
      copied=$((copied + 1))
    fi
  done < <(find "$template_dir" -type f | sort)
  echo "  seeded: $target_root ($copied copied, $kept already existed)"
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
    echo "finki-coordinator: DRY RUN - printing actions, writing nothing"
  else
    echo "finki-coordinator: applying configuration"
  fi
  step_env_check
  step_validate_config
  step_install_soul_md
  step_install_skills
  step_apply_hermes_config
  step_enable_plugin_toolsets
  step_seed_project_template
  step_next_steps
  echo "done."
}

main "$@"
