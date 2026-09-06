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
#   1/8 check required env keys (TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY)
#   2/8 validate config/config.yaml via 'python -m coordinator.config validate'
#   3/8 install prompts/persona.md -> ${HERMES_HOME:-$HOME/.hermes}/SOUL.md
#   4/8 install prompts/skills/*/SKILL.md -> ${HERMES_HOME:-$HOME/.hermes}/skills/
#       coordinator-<name>/SKILL.md (template-owned: overwrites on every run, same
#       precedent as the SOUL.md install; no rebuild/restart needed — the runtime
#       skill store lives on the ~/.hermes volume)
#   5/8 apply Hermes config: model + cron.model pin + timezone UTC ('hermes config set',
#       with a printed manual fallback when the CLI is absent)
#   6/8 enable the coordinator plugin + kanban toolset (plugins.enabled / top-level
#       toolsets — upstream opt-in gates; printed in-container fallback when the CLI
#       is absent, since the plugin dir only materializes inside the container)
#   7/8 seed data/project/ from project-template/ (first-boot files; existing files
#       are never overwritten)
#   8/9 Google-token permission check (T2.26): the token must be writable by the
#       container runtime uid — repair when possible, print the exact sudo remedy
#       otherwise — plus the exec-user guard: 'docker compose exec' defaults to
#       ROOT here, so token-writing commands need '--user <runtime uid>'.
#   9/9 Site rebuild cron (T2.30): install the host crontab line that rebuilds the
#       static site every 15 minutes (UTC, dumb and LLM-free — rule-11 spirit);
#       idempotent via a marker comment.
#
# Overridable env knobs (defaults keep production behavior; overrides exist for
# testability and relocated installs): HERMES_HOME, HERMES_MODEL, HERMES_UID,
# HERMES_GID (the container runtime identity the token must be writable by),
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

# stat differs between GNU (Linux host, the Pi) and BSD (macOS host, dev smoke
# tests): same st_uid/st_gid letters (%u/%g), different permission letter
# (GNU %a vs BSD %Lp — BSD %a is the access TIME).
if stat -c %u / >/dev/null 2>&1; then
  stat_uid()  { stat -c %u "$1"; }
  stat_gid()  { stat -c %g "$1"; }
  stat_mode() { stat -c %a "$1"; }
else
  stat_uid()  { stat -f %u "$1"; }
  stat_gid()  { stat -f %g "$1"; }
  stat_mode() { stat -f %Lp "$1"; }
fi

# True when the mode's write bit covers uid (POSIX owner/group/other triple;
# supplementary groups are not consulted — the real shapes are owner-euid or
# root-owned, exactly the 2026-09-05 root-owned-token class). Args:
#   uid gid owner-uid owner-gid mode(octal string, e.g. 644)
uid_can_write() {
  local uid=$1 gid=$2 owner=$3 ogid=$4 mode=$5
  if (( uid == 0 )); then return 0; fi
  if (( owner == uid )); then
    (( (8#$mode & 8#200) != 0 ))
  elif (( ogid == gid )); then
    (( (8#$mode & 8#020) != 0 ))
  else
    (( (8#$mode & 8#002) != 0 ))
  fi
}

step_env_check() {
  echo "== [1/9] Required env keys (names only; values are never printed) =="
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
  echo "== [2/9] Validate config/config.yaml (python -m coordinator.config validate) =="
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
  echo "== [3/9] SOUL.md (persona) =="
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
  echo "== [4/9] Coordinator skills -> runtime skill store (template-owned, overwrite) =="
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
  echo "== [5/9] Hermes config (model + cron.model pin + timezone UTC) =="
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
  echo "== [6/9] Coordinator plugin + kanban toolset (runtime config) =="
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
  echo "== [7/9] Seed data/project/ from project-template/ (existing files never overwritten) =="
  local template_dir="$REPO_ROOT/project-template"
  local target_root="$PROJECT_DATA_ROOT/project"
  if [[ ! -d "$template_dir" ]]; then
    echo "  WARNING: $template_dir not found - skipping the project seed" >&2
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD seed: $target_root (first boot; existing files are never overwritten)"
    echo "  WOULD ensure: $target_root/docs is a git repo (init + local identity if none + baseline commit)"
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

  # v7 (T2.29, proposal §12 invariant 1): the knowledge record needs LOCAL history —
  # make data/project/docs/ its own git repo, once, committable on a bare machine.
  # The daily 03:00 UTC backup job (T2.32) commits BEFORE it uploads; a repo that
  # cannot commit would violate the invariant this init exists for.
  local docs_dir="$target_root/docs"
  local did_init=0
  if [[ ! -d "$docs_dir/.git" ]]; then
    git -C "$docs_dir" init >/dev/null
    did_init=1
    echo "  git: initialized $docs_dir (the record's local history)"
  fi
  # Identity: set a LOCAL identity only when the operator has none (a global identity
  # is respected, never overridden — commit attribution stays the operator's).
  if ! git -C "$docs_dir" config user.name >/dev/null 2>&1; then
    git -C "$docs_dir" config user.name "Hermes Coordinator"
  fi
  if ! git -C "$docs_dir" config user.email >/dev/null 2>&1; then
    git -C "$docs_dir" config user.email "coordinator@localhost"
  fi
  # Baseline commit on first boot only: re-runs never commit operator changes —
  # that is the daily backup job's job, not setup.sh's.
  if [[ "$did_init" -eq 1 && -n "$(git -C "$docs_dir" status --porcelain)" ]]; then
    git -C "$docs_dir" add -A
    git -C "$docs_dir" -c commit.gpgsign=false commit -q -m "seed: baseline"
    echo "  git: baseline commit created"
  fi
}

step_check_google_token() {
  echo "== [8/9] Google token permissions (must be writable by the container runtime uid) =="
  local token_path="$HERMES_HOME_DIR/google_token.json"
  local container_uid="${HERMES_UID:-$(id -u)}"
  local container_gid="${HERMES_GID:-$(id -g)}"
  local numeric_re='^[0-9]+$'
  # A non-numeric uid/gid must not reach the arithmetic below: under set -u the
  # (( )) would abort the whole script with a cryptic unbound-variable error.
  if [[ ! "$container_uid" =~ $numeric_re || ! "$container_gid" =~ $numeric_re ]]; then
    echo "  WARNING: HERMES_UID/HERMES_GID must be numeric (got"
    echo "  '$container_uid'/'$container_gid') — skipping the token check (fix .env, re-run)."
  else
    echo "  checking: $token_path (container runtime uid $container_uid)"
    if [[ "$EUID" -eq 0 && -z "${HERMES_UID:-}" ]]; then
      echo "  WARNING: running as root with HERMES_UID unset — the container runtime uid"
      echo "  is unknowable here and the check below runs as uid 0 (root writes"
      echo "  anything), so a root-owned token would NOT be flagged. Set HERMES_UID."
    fi
    if [[ ! -f "$token_path" ]]; then
      echo "  absent: no Google token yet (created by the in-container OAuth —"
      echo "          docs/verify/phase2.md step 1; run it as the runtime user, see below)"
    else
      local owner ogid mode remedy
      owner="$(stat_uid "$token_path")"
      ogid="$(stat_gid "$token_path")"
      mode="$(stat_mode "$token_path")"
      if uid_can_write "$container_uid" "$container_gid" "$owner" "$ogid" "$mode"; then
        echo "  ok: owner $owner, mode $mode — writable by uid $container_uid"
      else
        echo "  WARNING: owner $owner, mode $mode — NOT writable by uid $container_uid."
        echo "  Every token refresh write by the runtime user fails in this state"
        echo "  (the 2026-09-05 root-owned-token incident; the gws CLI's token"
        echo "  refresh writes hit it)."
        if [[ "$owner" -eq "$container_uid" ]]; then
          remedy="chmod 600 $token_path"
        else
          remedy="sudo chown $container_uid:$container_gid $token_path && chmod 600 $token_path"
        fi
        if [[ "$DRY_RUN" -eq 1 ]]; then
          echo "  dry-run: WOULD apply: $remedy"
        elif [[ "$EUID" -eq 0 ]]; then
          chown "$container_uid:$container_gid" "$token_path"
          chmod 600 "$token_path"
          echo "  repaired: owner $container_uid, mode 600"
        elif [[ "$owner" -eq "$EUID" && "$owner" -eq "$container_uid" ]]; then
          chmod 600 "$token_path"
          echo "  repaired: mode 600 (you own the token and are the container uid)"
        else
          echo "  fix (run on the host):"
          echo "    $remedy"
        fi
      fi
    fi
  fi
  echo "  NOTE: docker compose exec defaults to ROOT in this container — any token-writing"
  echo "  command (OAuth setup.py, checks, the gws CLI) must run as the runtime user:"
  echo "    docker compose exec --user $container_uid gateway <command>"
}

step_install_site_cron() {
  echo "== [9/9] Site rebuild cron (every 15 min, UTC — dumb and LLM-free, rule 11) =="
  local marker="# hermes-coordinator: site rebuild (T2.30) — do not edit"
  # Quoted paths: the cron line survives repo locations with spaces.
  local cron_line="*/15 * * * * cd '${REPO_ROOT}' && make site-build >> '${REPO_ROOT}/data/site-build.log' 2>&1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  WOULD install crontab line (idempotent, marker-matched):"
    echo "    $marker"
    echo "    $cron_line"
    return 0
  fi
  if ! command -v crontab >/dev/null 2>&1; then
    echo "  WARNING: crontab not found - install the site rebuild line manually:" >&2
    echo "    $marker" >&2
    echo "    $cron_line" >&2
    return 0
  fi
  # Distinguish "no crontab yet" (normal) from a real crontab -l failure: blindly
  # treating every failure as empty would let the install below CLOBBER the
  # operator's existing crontab (review finding, T2.30).
  local current
  if current="$(crontab -l 2>&1)"; then
    :
  elif grep -qi "no crontab" <<<"$current"; then
    current=""
  else
    echo "  WARNING: cannot read the crontab - install the site rebuild line manually:" >&2
    echo "    $marker" >&2
    echo "    $cron_line" >&2
    return 0
  fi
  if grep -Fq "$marker" <<<"$current"; then
    echo "  ok: site rebuild cron already installed"
    return 0
  fi
  printf '%s\n%s\n%s\n' "$current" "$marker" "$cron_line" | crontab -
  echo "  installed: site rebuild cron (*/15 * * * *)"
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
  step_check_google_token
  step_install_site_cron
  step_next_steps
  echo "done."
}

main "$@"
