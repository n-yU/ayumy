#!/usr/bin/env bash
set -euo pipefail

AYUMY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SOURCE="$AYUMY_ROOT/hooks/post-commit"

usage() {
  cat <<'USAGE'
Usage: ayumy setup-hooks [options]

Install the post-commit hook to Git repositories via symlink.

Options:
  --all <dir>   Scan <dir> for Git repositories and install hooks to all of them
  --force       Overwrite an existing post-commit hook
  --help        Show this help message
USAGE
  exit 1
}

# Install the hook to a single repository.
# Arguments: $1 = path to .git directory
install_hook() {
  local git_dir="$1"
  local hook_dir="$git_dir/hooks"
  local hook_path="$hook_dir/post-commit"

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

  mkdir -p "$hook_dir"
  ln -sf "$HOOK_SOURCE" "$hook_path"
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
      usage
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
  failed=0
  for git_dir in "$all_dir"/*/.git; do
    [[ -d "$git_dir" ]] || continue
    found=$((found + 1))
    install_hook "$git_dir" || failed=$((failed + 1))
  done

  if [[ "$found" -eq 0 ]]; then
    echo "[ayumy] no Git repositories found in: $all_dir" >&2
    exit 1
  fi
  echo "[ayumy] done: $found repos found, $((found - failed)) installed, $failed skipped"
else
  # Install to the current directory's repository.
  git_dir="$(git rev-parse --git-dir 2>/dev/null)" || {
    echo "ayumy setup-hooks: not a Git repository" >&2
    exit 1
  }
  # Normalize to absolute path.
  git_dir="$(cd "$git_dir" && pwd)"
  install_hook "$git_dir" || true
fi
