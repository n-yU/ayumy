#!/usr/bin/env bash
set -euo pipefail

AYUMY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SOURCE="$AYUMY_ROOT/hooks/pre-push"
LEGACY_HOOK_SOURCE="$AYUMY_ROOT/hooks/post-commit"
CONFIG_FILE="$AYUMY_ROOT/lambda/config/config.yml"
ICON_COLORS="gray lightgray brown yellow orange green blue purple pink red"
DEFAULT_ICON_COLOR="gray"

usage() {
  local code="${1:-1}"
  local dest=1
  [[ "$code" -ne 0 ]] && dest=2
  cat >&"$dest" <<'USAGE'
Usage: ayumy setup-hooks [options]

Install the pre-push hook to the current repository via symlink,
then ask for the Notion page icon unless the repository already has one.
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

# Arguments: $1 = repository name
has_icon_entry() {
  # Escape dots so a name like `a.b` cannot match `axb`
  grep -q "^    ${1//./\\.}: {" "$CONFIG_FILE"
}

# Arguments: $1 = repository name
# Appends one entry to the repository_icons map, which the config file keeps last for this purpose.
configure_icon() {
  local repo_name="$1" name color

  # Appending to a missing file would produce a config holding nothing but one icon entry
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "[ayumy] config file not found: $CONFIG_FILE" >&2
    echo "[ayumy] run 'make config-init' in $AYUMY_ROOT, then rerun 'ayumy setup-hooks'" >&2
    return 1
  fi

  if has_icon_entry "$repo_name"; then
    echo "[ayumy] Notion icon already configured: $repo_name"
    return 0
  fi

  echo "[ayumy] Notion page icon for $repo_name (name as shown in the Notion icon picker)"
  read -r -p "  icon name: " name || true
  if [[ -z "$name" ]]; then
    echo "[ayumy] icon name is required; rerun 'ayumy setup-hooks' or add the entry to $CONFIG_FILE by hand" >&2
    return 1
  fi

  read -r -p "  color [$DEFAULT_ICON_COLOR] ($ICON_COLORS): " color || true
  color="${color:-$DEFAULT_ICON_COLOR}"
  case " $ICON_COLORS " in
    *" $color "*) ;;
    *)
      echo "[ayumy] unknown color '$color'; rerun 'ayumy setup-hooks' or add the entry to $CONFIG_FILE by hand" >&2
      return 1
      ;;
  esac

  printf '    %s: {name: %s, color: %s}\n' "$repo_name" "$name" "$color" >> "$CONFIG_FILE"
  echo "[ayumy] recorded icon in $CONFIG_FILE; run 'make lambda-deploy' to apply"
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

if [[ "$rc" -eq 0 ]]; then
  remote_url="$(git remote get-url origin 2>/dev/null || true)"
  repo_name="$(echo "$remote_url" | sed 's|.*[:/]||; s|\.git$||')"
  if [[ -z "$repo_name" ]]; then
    # Reports are keyed by repository name, so a remote-less repo has nothing to attach an icon to
    echo "[ayumy] no origin remote; skipped Notion icon setup" >&2
  else
    # The icon is required per repository, so an unanswered or invalid prompt fails the command
    configure_icon "$repo_name" || exit 1
  fi
fi
