#!/usr/bin/env bash
set -euo pipefail
umask 077

CLAUDE_PROJECTS_DIR="$HOME/.claude/projects"
MARKER_NAME=".ayumy_last_sync"

# --- helpers ---

usage() {
  cat <<'USAGE'
Usage: sync_session.sh [--project <name>] [--all] [--report] [--date DATE]

Options:
  --project <name>    Sync a specific project
  --all               Sync all projects
  --report            Invoke Lambda to generate report after sync
  --date DATE         Generate report for a specific date or range (requires --report)
                      Formats: YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD
  (no args)           Auto-detect project from current directory

Environment variables:
  AYUMY_S3_BUCKET        S3 bucket name for session storage (required)
  AYUMY_LAMBDA_FUNCTION  Lambda function name (required for --report)
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

# Sync a single project. Returns 0 if files were transferred, 1 if nothing to do, 2 on transfer error.
sync_project() {
  local project_dir="$1"
  local project_name
  project_name=$(basename "$project_dir")

  # Record sync start time before scanning to avoid race conditions.
  # Any JSONL modified between scan and copy will have mtime newer than
  # this marker and will be picked up on the next run.
  local tmp_marker="$project_dir/${MARKER_NAME}.tmp"
  touch "$tmp_marker"
  trap 'rm -f "$tmp_marker"' RETURN

  local files
  files=$(find_changed_sessions "$project_dir")

  if [[ -z "$files" ]]; then
    log "$project_name: no changes"
    return 1
  fi

  local file_count
  file_count=$(echo "$files" | wc -l | tr -d ' ')
  log "$project_name: syncing $file_count session(s)"

  local dest_prefix="s3://$AYUMY_S3_BUCKET/claude-sessions/$project_name/"
  while IFS= read -r f; do
    if ! aws s3 cp "$f" "$dest_prefix" --quiet; then
      err "$project_name: failed to upload $(basename -- "$f")"
      return 2
    fi
  done <<< "$files"

  # Upload repo name metadata if available
  local repo_file="$project_dir/.ayumy_repo"
  if [[ -f "$repo_file" ]]; then
    if ! aws s3 cp "$repo_file" "${dest_prefix}.ayumy_repo" --quiet; then
      err "$project_name: failed to upload .ayumy_repo metadata"
      return 2
    fi
  fi

  # Promote temp marker to actual marker on success
  mv "$tmp_marker" "$project_dir/$MARKER_NAME"
  log "$project_name: done"
  return 0
}

# --- option parsing ---

mode=""
project_name=""
report=false
target_date=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ -n "$mode" && "$mode" != "project" ]] && { err "conflicting options: --project and --all"; usage; }
      mode="project"
      project_name="${2:-}"
      [[ -z "$project_name" ]] && { err "--project requires a name"; usage; }
      shift 2
      ;;
    --all)
      [[ -n "$mode" && "$mode" != "all" ]] && { err "conflicting options: --project and --all"; usage; }
      mode="all"
      shift
      ;;
    --report)
      report=true
      shift
      ;;
    --date)
      target_date="${2:-}"
      [[ -z "$target_date" ]] && { err "--date requires a value"; usage; }
      if ! [[ "$target_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}(\.\.[0-9]{4}-[0-9]{2}-[0-9]{2})?$ ]]; then
        err "--date must be YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD"
        usage
      fi
      shift 2
      ;;
    *)
      err "unknown option: $1"
      usage
      ;;
  esac
done

if [[ -n "$target_date" && "$report" != true ]]; then
  err "--date requires --report"
  usage
fi

# --- validation ---

if ! command -v aws &>/dev/null; then
  err "aws CLI is not installed"
  exit 1
fi

if [[ -z "${AYUMY_S3_BUCKET:-}" ]]; then
  err "AYUMY_S3_BUCKET is not set"
  exit 1
fi

# Normalize: strip s3:// prefix and trailing slashes
AYUMY_S3_BUCKET="${AYUMY_S3_BUCKET#s3://}"
while [[ "$AYUMY_S3_BUCKET" == */ ]]; do
  AYUMY_S3_BUCKET="${AYUMY_S3_BUCKET%/}"
done

if [[ -z "$AYUMY_S3_BUCKET" || "$AYUMY_S3_BUCKET" == */* ]]; then
  err "AYUMY_S3_BUCKET must be a plain bucket name (no s3:// prefix, no path): '$AYUMY_S3_BUCKET'"
  exit 1
fi

if [[ "$report" == true ]]; then
  if [[ -z "${AYUMY_LAMBDA_FUNCTION:-}" ]]; then
    err "AYUMY_LAMBDA_FUNCTION is not set (required for --report)"
    exit 1
  fi
  # --cli-binary-format is AWS CLI v2 only
  aws_version=$(aws --version 2>&1 | grep -oE 'aws-cli/[0-9]+' | grep -oE '[0-9]+' || true)
  if [[ -z "$aws_version" || "$aws_version" -lt 2 ]]; then
    err "aws CLI v2 or later is required for --report (found v${aws_version:-unknown})"
    exit 1
  fi
fi

if [[ ! -d "$CLAUDE_PROJECTS_DIR" ]]; then
  err "$CLAUDE_PROJECTS_DIR does not exist"
  exit 1
fi

# --- main ---

case "$mode" in
  project)
    project_dir="$CLAUDE_PROJECTS_DIR/$project_name"
    if [[ ! -d "$project_dir" ]]; then
      err "project not found: $project_dir"
      exit 1
    fi
    rc=0; sync_project "$project_dir" || rc=$?
    [[ "$rc" -eq 0 || "$rc" -eq 1 ]] || exit "$rc"
    ;;
  all)
    synced=0
    for project_dir in "$CLAUDE_PROJECTS_DIR"/*/; do
      [[ -d "$project_dir" ]] || continue
      rc=0; sync_project "$project_dir" || rc=$?
      if [[ "$rc" -eq 0 ]]; then
        synced=$((synced + 1))
      elif [[ "$rc" -ne 1 ]]; then
        exit "$rc"
      fi
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
    rc=0; sync_project "$project_dir" || rc=$?
    [[ "$rc" -eq 0 || "$rc" -eq 1 ]] || exit "$rc"
    ;;
esac

# --- report mode: invoke Lambda ---

if [[ "$report" == true ]]; then
  payload='{"source": "manual"}'
  if [[ -n "$target_date" ]]; then
    payload="{\"source\": \"manual\", \"target_date\": \"$target_date\"}"
  fi

  log "invoking Lambda function: $AYUMY_LAMBDA_FUNCTION (async)"
  aws lambda invoke \
    --function-name "$AYUMY_LAMBDA_FUNCTION" \
    --invocation-type Event \
    --payload "$payload" \
    --cli-binary-format raw-in-base64-out \
    /dev/null > /dev/null
  log "Lambda invocation accepted — check Slack for results"
fi
