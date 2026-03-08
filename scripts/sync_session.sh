#!/usr/bin/env bash
set -euo pipefail

CLAUDE_PROJECTS_DIR="$HOME/.claude/projects"
MARKER_NAME=".ayumy_last_sync"

# --- helpers ---

usage() {
  cat <<'USAGE'
Usage: sync_session.sh [--project <name>] [--all] [--background]

Options:
  --project <name>  Sync a specific project
  --all             Sync all projects
  --background      Run in the background (for hooks)
  (no args)         Auto-detect project from current directory
USAGE
  exit 1
}

log() { echo "[ayumy] $*"; }
err() { echo "[ayumy] ERROR: $*" >&2; }

# Convert an absolute path to the Claude project directory name.
# e.g. /Users/username/Documents/github/ayumy -> -Users-username-Documents-github-ayumy
path_to_project_name() {
  echo "$1" | sed 's|/|-|g'
}

# Find JSONL files newer than the marker, or all if no marker exists.
find_changed_sessions() {
  local project_dir="$1"
  local marker="$project_dir/$MARKER_NAME"

  if [[ -f "$marker" ]]; then
    find "$project_dir" -maxdepth 1 -name "*.jsonl" -newer "$marker" -print
  else
    find "$project_dir" -maxdepth 1 -name "*.jsonl" -print
  fi
}

# Sync a single project. Returns 0 if files were transferred, 1 if nothing to do.
sync_project() {
  local project_dir="$1"
  local project_name
  project_name=$(basename "$project_dir")

  # Record sync start time before scanning to avoid race conditions.
  # Any JSONL modified between scan and copy will have mtime newer than
  # this marker and will be picked up on the next run.
  local tmp_marker="$project_dir/${MARKER_NAME}.tmp"
  touch "$tmp_marker"

  local files
  files=$(find_changed_sessions "$project_dir")

  if [[ -z "$files" ]]; then
    rm -f "$tmp_marker"
    log "$project_name: no changes"
    return 1
  fi

  local file_count
  file_count=$(echo "$files" | wc -l | tr -d ' ')
  log "$project_name: syncing $file_count session(s)"

  local dest_dir="$AYUMY_DATA_DIR/claude-sessions/$project_name"
  mkdir -p "$dest_dir"
  echo "$files" | while IFS= read -r f; do
    cp "$f" "$dest_dir/"
  done

  # Promote temp marker to actual marker on success
  mv "$tmp_marker" "$project_dir/$MARKER_NAME"
  log "$project_name: done"
  return 0
}

# --- option parsing ---

mode=""
project_name=""
background=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      mode="project"
      project_name="${2:-}"
      [[ -z "$project_name" ]] && { err "--project requires a name"; usage; }
      shift 2
      ;;
    --all)
      mode="all"
      shift
      ;;
    --background)
      background=true
      shift
      ;;
    *)
      err "unknown option: $1"
      usage
      ;;
  esac
done

# --- validation ---

if [[ -z "${AYUMY_DATA_DIR:-}" ]]; then
  err "AYUMY_DATA_DIR is not set"
  exit 1
fi

if [[ ! -d "$CLAUDE_PROJECTS_DIR" ]]; then
  err "$CLAUDE_PROJECTS_DIR does not exist"
  exit 1
fi

# --- background mode: re-exec detached ---

if [[ "$background" == true ]]; then
  args=()
  [[ "$mode" == "project" ]] && args+=(--project "$project_name")
  [[ "$mode" == "all" ]] && args+=(--all)
  nohup "$0" "${args[@]}" >/dev/null 2>&1 &
  exit 0
fi

# --- main ---

case "$mode" in
  project)
    project_dir="$CLAUDE_PROJECTS_DIR/$project_name"
    if [[ ! -d "$project_dir" ]]; then
      err "project not found: $project_dir"
      exit 1
    fi
    sync_project "$project_dir"
    ;;
  all)
    synced=0
    for project_dir in "$CLAUDE_PROJECTS_DIR"/*/; do
      [[ -d "$project_dir" ]] || continue
      sync_project "$project_dir" && synced=$((synced + 1)) || true
    done
    log "synced $synced project(s)"
    ;;
  "")
    # Auto-detect from current directory
    git_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
      err "not in a git repository (use --project or --all)"
      exit 1
    }
    project_name=$(path_to_project_name "$git_root")
    project_dir="$CLAUDE_PROJECTS_DIR/$project_name"
    if [[ ! -d "$project_dir" ]]; then
      err "no Claude sessions found for $git_root"
      exit 1
    fi
    sync_project "$project_dir"
    ;;
esac
