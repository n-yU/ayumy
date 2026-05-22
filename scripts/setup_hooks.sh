#!/usr/bin/env bash
set -euo pipefail

AYUMY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SOURCE="$AYUMY_ROOT/hooks/pre-push"
LEGACY_HOOK_SOURCE="$AYUMY_ROOT/hooks/post-commit"

# Verify that the hook source exists (and is executable) before proceeding.
if [[ ! -f "$HOOK_SOURCE" || ! -x "$HOOK_SOURCE" ]]; then
  echo "ayumy setup-hooks: hook source not found or not executable: $HOOK_SOURCE" >&2
  exit 1
fi

usage() {
  cat <<'USAGE'
Usage: ayumy setup-hooks [options]

Install the pre-push hook to Git repositories via symlink.
Also removes any legacy post-commit symlink that points at ayumy/hooks/post-commit.

Options:
  --all <dir>   Scan immediate children of <dir> for Git repositories and install hooks
  --force       Overwrite an existing pre-push hook
  --help        Show this help message
USAGE
  exit "${1:-1}"
}

# Remove a legacy post-commit symlink that points at the old ayumy hook.
# Returns 0 if a symlink was removed, 1 otherwise.
# Only removes symlinks that match LEGACY_HOOK_SOURCE; foreign hooks are left untouched.
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

# Install the hook to a single repository.
# Arguments: $1 = path to .git directory
install_hook() {
  local git_dir="$1"
  local hook_dir="$git_dir/hooks"
  local hook_path="$hook_dir/pre-push"

  remove_legacy_post_commit "$hook_dir" || true

  if [[ -L "$hook_path" ]]; then
    local target
    if target="$(readlink "$hook_path" 2>/dev/null)"; then
      if [[ "$target" == "$HOOK_SOURCE" ]]; then
        echo "[ayumy] already installed: $hook_path"
        return 0
      fi
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
all_dir=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      [[ $# -lt 2 ]] && { echo "ayumy setup-hooks: --all requires a directory" >&2; exit 1; }
      all_dir="$2"
      shift 2
      ;;
    --force)
      force="true"
      shift
      ;;
    --help)
      usage 0
      ;;
    *)
      echo "ayumy setup-hooks: unknown option '$1'" >&2
      usage
      ;;
  esac
done

if [[ -n "$all_dir" ]]; then
  if [[ ! -d "$all_dir" ]]; then
    echo "ayumy setup-hooks: directory not found: $all_dir" >&2
    exit 1
  fi

  found=0
  skipped=0
  errors=0
  for git_dir in "$all_dir"/*/.git; do
    [[ -d "$git_dir" ]] || continue
    found=$((found + 1))
    rc=0
    install_hook "$git_dir" || rc=$?
    if [[ "$rc" -eq 1 ]]; then
      skipped=$((skipped + 1))
    elif [[ "$rc" -ge 2 ]]; then
      errors=$((errors + 1))
    fi
  done

  if [[ "$found" -eq 0 ]]; then
    echo "[ayumy] no Git repositories found in: $all_dir" >&2
    exit 1
  fi
  installed=$((found - skipped - errors))
  summary="[ayumy] done: $found repos found, $installed installed, $skipped skipped"
  [[ "$errors" -gt 0 ]] && summary="$summary, $errors failed"
  echo "$summary"
  if [[ "$errors" -gt 0 ]]; then
    exit 2
  fi
else
  # Install to the current directory's repository.
  git_dir="$(git rev-parse --git-dir 2>/dev/null)" || {
    echo "ayumy setup-hooks: not a Git repository" >&2
    exit 1
  }
  # Worktrees and submodules use a .git file instead of a directory; not supported.
  if [[ ! -d "$git_dir" ]]; then
    echo "ayumy setup-hooks: unsupported Git layout (.git is not a directory): $git_dir" >&2
    exit 1
  fi
  # Normalize to absolute path.
  git_dir="$(cd "$git_dir" && pwd)"
  rc=0
  install_hook "$git_dir" || rc=$?
  # Exit with error code for real failures; skip (rc=1) is non-fatal.
  if [[ "$rc" -ge 2 ]]; then
    exit "$rc"
  fi
fi
