#!/usr/bin/env bash
set -euo pipefail

AYUMY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SOURCE="$AYUMY_ROOT/hooks/pre-push"
LEGACY_HOOK_SOURCE="$AYUMY_ROOT/hooks/post-commit"

usage() {
  local code="${1:-1}"
  local dest=1
  [[ "$code" -ne 0 ]] && dest=2
  cat >&"$dest" <<'USAGE'
Usage: ayumy setup-hooks [options]

Install the pre-push hook to the current repository via symlink.
Also removes legacy post-commit symlinks created by this script;
ones installed by hand with a different path are left untouched.

Options:
  --force       Overwrite an existing pre-push hook
  -h, --help    Show this help message
USAGE
  exit "$code"
}

remove_legacy_post_commit() {
  local hook_dir="$1"
  local legacy_path="$hook_dir/post-commit"

  [[ -L "$legacy_path" ]] || return 1
  local target
  target="$(readlink "$legacy_path" 2>/dev/null)" || return 1
  [[ "$target" == "$LEGACY_HOOK_SOURCE" ]] || return 1

  if ! rm "$legacy_path"; then
    echo "[ayumy] failed to remove legacy post-commit symlink: $legacy_path" >&2
    return 1
  fi
  echo "[ayumy] removed legacy post-commit symlink: $legacy_path"
  return 0
}

# Arguments: $1 = path to the hooks directory Git reads
install_hook() {
  local hook_dir="$1"
  local hook_path="$hook_dir/pre-push"

  remove_legacy_post_commit "$hook_dir" || true

  if [[ -L "$hook_path" ]]; then
    local target
    target="$(readlink "$hook_path" 2>/dev/null)" || target=""
    if [[ "$target" == "$HOOK_SOURCE" ]]; then
      echo "[ayumy] already installed: $hook_path"
      return 0
    fi
  fi

  if [[ -e "$hook_path" && "$force" != "true" ]]; then
    echo "[ayumy] skipped (existing hook found, use --force to overwrite): $hook_path" >&2
    return 1
  fi

  if ! mkdir -p "$hook_dir"; then
    echo "[ayumy] failed to create hook directory: $hook_dir" >&2
    return 2
  fi

  if ! ln -sf "$HOOK_SOURCE" "$hook_path"; then
    echo "[ayumy] failed to install hook (could not create symlink): $hook_path -> $HOOK_SOURCE" >&2
    return 2
  fi
  echo "[ayumy] installed: $hook_path"
}

force="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      force="true"
      shift
      ;;
    -h|--help)
      usage 0
      ;;
    *)
      echo "ayumy setup-hooks: unknown option '$1'" >&2
      usage
      ;;
  esac
done

# Runs after option parsing so --help / -h still work when the hook source is missing or non-executable.
if [[ ! -f "$HOOK_SOURCE" || ! -x "$HOOK_SOURCE" ]]; then
  echo "ayumy setup-hooks: hook source not found or not executable: $HOOK_SOURCE" >&2
  exit 1
fi

# A linked worktree keeps its hooks in the common dir, so ask Git for the path instead of deriving it.
hook_dir="$(git rev-parse --git-path hooks 2>/dev/null)" || {
  echo "ayumy setup-hooks: not a Git repository" >&2
  exit 1
}
# Normalize to absolute path via the parent, since the hooks directory itself may not exist yet.
hook_dir="$(cd "$(dirname "$hook_dir")" && pwd)/$(basename "$hook_dir")"
rc=0
install_hook "$hook_dir" || rc=$?
# Exit with error code for real failures; skip (rc=1) is non-fatal.
if [[ "$rc" -ge 2 ]]; then
  exit "$rc"
fi
