#!/usr/bin/env bash
# scripts/allow.sh - the operator door (D1): add Telegram user IDs to the allowlist.
#
# The bot never edits its own authorization: this script is run by the OWNER. It
# appends the missing numeric IDs to TELEGRAM_ALLOWED_USERS in the gitignored .env
# (idempotent; every other byte untouched; no other .env value is ever printed) and
# applies with 'docker compose up -d' - NEVER 'docker compose restart', which reuses
# the env frozen at container creation and would silently ignore the new line
# (proposal section 8.2). Member rows are completed afterwards by the agent via
# member_add/member_update with the sender's ID from session context.
#
# Usage: scripts/allow.sh [--dry-run] <id>...
#   <id>      numeric Telegram user ID (from @userinfobot or similar); repeatable
#   --dry-run print the actions, write nothing, run nothing
#
# Overridable knob (testability): ALLOW_ENV_FILE (default $REPO_ROOT/.env).
#
# Exit codes: 0 ok (including the no-op case) | 2 usage error | 1 .env/apply failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ALLOW_ENV_FILE:-$REPO_ROOT/.env}"
KEY="TELEGRAM_ALLOWED_USERS"

usage() {
  echo "usage: scripts/allow.sh [--dry-run] <id>...   # numeric Telegram user IDs" >&2
}

DRY_RUN=0
IDS=()
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -*) echo "error: unknown option $arg" >&2; usage; exit 2 ;;
    *)
      if [[ ! "$arg" =~ ^[0-9]+$ ]]; then
        echo "error: '$arg' is not a numeric Telegram user ID (digits only)" >&2
        usage
        exit 2
      fi
      IDS+=("$arg")
      ;;
  esac
done
if [[ ${#IDS[@]} -eq 0 ]]; then
  usage
  exit 2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found - copy .env.example to .env first:" >&2
  echo "       cp .env.example .env" >&2
  exit 1
fi

# The file must carry exactly one allowlist line: two would make the edit ambiguous.
count=$(grep -c "^${KEY}=" "$ENV_FILE" || true)
if [[ "$count" -gt 1 ]]; then
  echo "error: ${KEY} appears $count times in $ENV_FILE - keep one line and re-run" >&2
  exit 1
fi

# Parse the current value: strip the key, drop the inline comment, split, trim.
existing=()
raw_line=""
if [[ "$count" -eq 1 ]]; then
  raw_line=$(grep -m1 "^${KEY}=" "$ENV_FILE")
  raw_value="${raw_line#${KEY}=}"
  raw_value="${raw_value%%#*}"              # drop the "#"-comment onward (IDs are digits)
  tokens=()
  IFS="," read -r -a tokens <<< "$raw_value" || true
  for token in "${tokens[@]}"; do
    token="$(printf '%s' "$token" | tr -d '[:space:]')"
    [[ -n "$token" ]] && existing+=("$token")
  done
fi

# Which requested IDs are missing? (Already-present IDs are the idempotent case.)
missing=()
for id in "${IDS[@]}"; do
  seen=0
  for have in "${existing[@]:-}"; do
    [[ -n "$have" && "$have" == "$id" ]] && { seen=1; break; }
  done
  [[ "$seen" -eq 0 ]] && missing+=("$id")
done

if [[ ${#missing[@]} -eq 0 ]]; then
  echo "all requested IDs already in ${KEY}; nothing to do (no apply needed)"
  exit 0
fi

new_value=""
for have in "${existing[@]:-}"; do
  [[ -n "$have" ]] || continue
  [[ -n "$new_value" ]] && new_value="$new_value,"
  new_value="$new_value$have"
done
for id in "${missing[@]}"; do
  [[ -n "$new_value" ]] && new_value="$new_value,"
  new_value="$new_value$id"
done

# Rebuild the line, preserving any inline comment the operator file carried.
comment=""
if [[ "$count" -eq 1 && "$raw_line" == *"#"* ]]; then
  comment="${raw_line#*#}"                  # text after the first "#"
  comment="#${comment}"                     # keep the "#" itself
fi
new_line="${KEY}=${new_value}"
[[ -n "$comment" ]] && new_line="${new_line}  ${comment}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "WOULD add to ${KEY}: ${missing[*]}"
  echo "WOULD set:  ${new_line}"
  echo "WOULD run:  docker compose up -d"
  echo "  (env changes need a container recreate - compose handles that)"
  exit 0
fi

# Apply: rewrite ONLY the allowlist line (head/tail byte-exact line surgery), or
# append the line at EOF. Everything else in the file stays byte-identical.
tmp="$(mktemp "${ENV_FILE}.allowXXXXXX")"
trap 'rm -f "$tmp"' EXIT
if [[ "$count" -eq 1 ]]; then
  line_no=$(grep -n "^${KEY}=" "$ENV_FILE" | head -1 | cut -d: -f1)
  head -n $((line_no - 1)) "$ENV_FILE" > "$tmp"
  printf '%s\n' "$new_line" >> "$tmp"
  tail -n +"$((line_no + 1))" "$ENV_FILE" >> "$tmp"
else
  # Keep byte-fidelity when the file does not end with a newline.
  if [[ -n "$(tail -c 1 "$ENV_FILE")" ]]; then
    printf '\n' >> "$tmp"
    cat "$ENV_FILE" >> "$tmp"
  else
    cat "$ENV_FILE" >> "$tmp"
  fi
  printf '%s\n' "$new_line" >> "$tmp"
fi
mv "$tmp" "$ENV_FILE"
trap - EXIT

echo "added to ${KEY}: ${missing[*]}"
echo "${KEY} is now: ${new_value}"

# Apply the new env: recreate the container (compose detects the env change).
# NOT restart: a running process never re-reads its frozen environment.
if ! command -v docker >/dev/null 2>&1; then
  echo "error: $ENV_FILE was updated but docker is not on PATH to apply it." >&2
  echo "       run manually from the repo root:  docker compose up -d" >&2
  exit 1
fi
(cd "$REPO_ROOT" && docker compose up -d)
echo "applied: docker compose up -d (container recreated with the new allowlist)"
