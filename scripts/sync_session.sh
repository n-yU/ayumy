#!/usr/bin/env bash
set -euo pipefail
umask 077

CLAUDE_PROJECTS_DIR="$HOME/.claude/projects"
MARKER_NAME=".ayumy_last_sync"
REPO_NAME=".ayumy_repo"

# --- helpers ---

usage() {
  local code="${1:-1}"
  local dest=1
  [[ "$code" -ne 0 ]] && dest=2
  cat >&"$dest" <<'USAGE'
Usage: sync_session.sh [--project <name>] [--cwd <dir>] [--repo <name>] [--all] [--report] [--date DATE]

Options:
  --project <name>    Sync a specific project
  --cwd <dir>         Sync the projects holding sessions opened in <dir>
  --repo <name>       Record <name> as the repository of the synced projects (requires --cwd)
  --all               Sync all projects
  --report            Invoke Lambda to generate report after sync
  --date DATE         Generate report for a specific date or range (requires --report)
                      Formats: YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD
  -h, --help          Show this help message
  (no args)           Auto-detect project from current directory

Environment variables:
  AYUMY_S3_BUCKET        S3 bucket name for session storage (required)
  AYUMY_LAMBDA_FUNCTION  Lambda function name (required for --report)
USAGE
  exit "$code"
}

log() { echo "[ayumy] $*"; }
err() { echo "[ayumy] ERROR: $*" >&2; }

# Convert an absolute path to the Claude project directory name.
# e.g. /Users/username/repo.worktrees/topic -> -Users-username-repo-worktrees-topic
path_to_project_name() {
  echo "$1" | sed 's|[/.]|-|g'
}

# Report whether $1 holds sessions and none of them records a cwd.
project_predates_cwd() {
  local project_dir="$1" f found=1

  for f in "$project_dir"/*.jsonl; do
    [[ -f "$f" ]] || continue
    found=0
    grep -q -m 1 '"cwd":"[^"][^"]*"' "$f" || continue
    return 1
  done
  return "$found"
}

# Print the first cwd recorded in the session log $1.
session_cwd() {
  local field
  field="$(grep -o -m 1 '"cwd":"[^"][^"]*"' "$1" || true)"
  [[ -n "$field" ]] || return 1
  field="${field#\"cwd\":\"}"
  echo "${field%\"}"
}

# Report whether $1 holds a session opened in $2.
project_opened_in() {
  local project_dir="$1" target="$2" f

  for f in "$project_dir"/*.jsonl; do
    [[ -f "$f" ]] || continue
    # The first recorded cwd is the project's own; later entries can sit in another repository
    [[ "$(session_cwd "$f" || true)" == "$target" ]] || continue
    return 0
  done
  return 1
}

# Report whether $1 is named after $2 and holds a session that moved into $2.
project_switched_to() {
  local project_dir="$1" target="$2" f

  # Claude Code names the project after the directory a session moves into, yet its first cwd stays behind
  [[ "${project_dir##*/}" == "$(path_to_project_name "$target")" ]] || return 1
  for f in "$project_dir"/*.jsonl; do
    [[ -f "$f" ]] || continue
    # The name alone collides across paths that differ only in dots and separators
    grep -q -F "\"cwd\":\"$target\"" "$f" || continue
    return 0
  done
  return 1
}

# Print the repository name of the directory $1 was opened in.
resolve_repo_name() {
  local project_dir="$1" f cwd url name

  # Every session records the same directory, so any of them that still exists answers
  for f in "$project_dir"/*.jsonl; do
    [[ -f "$f" ]] || continue
    cwd="$(session_cwd "$f")" || continue
    # A worktree removed after its sessions were recorded leaves nothing to ask
    [[ -d "$cwd" ]] || continue
    url="$(cd -- "$cwd" 2>/dev/null && git remote get-url origin 2>/dev/null)" || continue
    # Kept in step with the name the hook derives from the push destination
    name="$(echo "$url" | sed 's|.*[:/]||; s|\.git$||')"
    [[ -n "$name" ]] || continue
    echo "$name"
    return 0
  done
  return 1
}

# Print the project directories holding sessions opened in or moved into $1, one per line.
resolve_project_dirs() {
  local target="$1"
  # Keep the root itself while dropping every other trailing slash, since a recorded cwd carries none
  while [[ "$target" == */ && "$target" != "/" ]]; do
    target="${target%/}"
  done

  # Claude Code rewrites dots as well as separators, so a name built from the path cannot be trusted
  local rc=1 candidate
  for candidate in "$CLAUDE_PROJECTS_DIR"/*/; do
    candidate="${candidate%/}"
    [[ -d "$candidate" ]] || continue
    project_opened_in "$candidate" "$target" || project_switched_to "$candidate" "$target" || continue
    echo "$candidate"
    rc=0
  done
  [[ "$rc" -eq 0 ]] && return 0

  # Sessions predating the cwd field cannot be matched by content, so fall back to the name Claude Code derives
  local derived="$CLAUDE_PROJECTS_DIR/$(path_to_project_name "$target")"
  if [[ -d "$derived" ]] && project_predates_cwd "$derived"; then
    echo "$derived"
    return 0
  fi
  return 1
}

find_changed_sessions() {
  local project_dir="$1"
  local marker="$project_dir/$MARKER_NAME"

  if [[ -f "$marker" ]]; then
    find "$project_dir" -maxdepth 1 -name "*.jsonl" -newer "$marker" -print
  else
    find "$project_dir" -maxdepth 1 -name "*.jsonl" -print
  fi
}

# Returns: 0 = transferred, 1 = no changes, 2 = transfer error.
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

  # A recorded name comes from the push destination, which the origin remote contradicts on a fork
  local repo_file="$project_dir/$REPO_NAME"
  local resolved
  if [[ ! -f "$repo_file" ]]; then
    if resolved="$(resolve_repo_name "$project_dir")"; then
      echo "$resolved" > "$repo_file"
    elif [[ -n "$files" ]]; then
      # Lambda leaves sessions of an unknown repository in S3 rather than ingesting them
      log "$project_name: skipped (repository unresolved)"
      return 1
    fi
  fi

  local dest_prefix="s3://$AYUMY_S3_BUCKET/claude-sessions/$project_name/"

  # Uploaded ahead of the JSONL check so a repo name recorded after the last sync still reaches S3
  if [[ -f "$repo_file" ]]; then
    if ! aws s3 cp "$repo_file" "${dest_prefix}${REPO_NAME}" --quiet; then
      err "$project_name: failed to upload $REPO_NAME metadata"
      return 2
    fi
  fi

  if [[ -z "$files" ]]; then
    log "$project_name: no changes"
    return 1
  fi

  local file_count
  file_count=$(echo "$files" | wc -l | tr -d ' ')
  log "$project_name: syncing $file_count session(s)"

  while IFS= read -r f; do
    if ! aws s3 cp "$f" "$dest_prefix" --quiet; then
      err "$project_name: failed to upload $(basename -- "$f")"
      return 2
    fi
  done <<< "$files"

  mv "$tmp_marker" "$project_dir/$MARKER_NAME"
  log "$project_name: done"
  return 0
}

# --- option parsing ---

mode=""
project_name=""
target_cwd=""
repo_name=""
dirs=""
report=false
target_date=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage 0
      ;;
    --project)
      [[ -n "$mode" && "$mode" != "project" ]] && { err "conflicting options: --project and --$mode"; usage; }
      mode="project"
      project_name="${2:-}"
      [[ -z "$project_name" ]] && { err "--project requires a name"; usage; }
      shift 2
      ;;
    --cwd)
      [[ -n "$mode" && "$mode" != "cwd" ]] && { err "conflicting options: --cwd and --$mode"; usage; }
      mode="cwd"
      target_cwd="${2:-}"
      [[ -z "$target_cwd" ]] && { err "--cwd requires a directory"; usage; }
      shift 2
      ;;
    --repo)
      repo_name="${2:-}"
      [[ -z "$repo_name" ]] && { err "--repo requires a name"; usage; }
      shift 2
      ;;
    --all)
      [[ -n "$mode" && "$mode" != "all" ]] && { err "conflicting options: --all and --$mode"; usage; }
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

if [[ -n "$repo_name" && "$mode" != "cwd" ]]; then
  err "--repo requires --cwd"
  usage
fi

# --- resolve targets for the working-directory modes ---

# Resolved before the AWS checks so a repository holding no sessions never blocks a push
if [[ "$mode" == "cwd" || -z "$mode" ]]; then
  if [[ -z "$mode" ]]; then
    target_cwd=$(git rev-parse --show-toplevel 2>/dev/null) || {
      err "not in a git repository (use --project, --cwd or --all)"
      exit 1
    }
  fi
  if [[ -d "$target_cwd" ]]; then
    # A session records the canonical path, which `..` segments and relative forms do not match
    target_cwd="$(cd -- "$target_cwd" 2>/dev/null && pwd)" || {
      err "--cwd could not be resolved: $target_cwd"
      exit 1
    }
  elif [[ "$target_cwd" != /* ]]; then
    # A deleted worktree keeps its sessions, so only an existing directory can be made absolute
    err "--cwd must be an absolute path when the directory does not exist"
    exit 1
  fi
  # Sessions of a deleted worktree stay resolvable, so the directory need not exist
  dirs="$(resolve_project_dirs "$target_cwd" || true)"
  if [[ -z "$dirs" ]]; then
    log "no sessions recorded for $target_cwd"
    # Reporting still needs the AWS checks below, so only a plain sync stops here
    [[ "$report" == true ]] || exit 0
  fi
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

# A missing directory is an absence of sessions for the working-directory modes, where failing here would block the push
if [[ ! -d "$CLAUDE_PROJECTS_DIR" && ( "$mode" == "project" || "$mode" == "all" ) ]]; then
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
  cwd|"")
    while IFS= read -r project_dir; do
      [[ -n "$project_dir" ]] || continue
      if [[ -n "$repo_name" ]]; then
        echo "$repo_name" > "$project_dir/$REPO_NAME"
      fi
      rc=0; sync_project "$project_dir" || rc=$?
      [[ "$rc" -eq 0 || "$rc" -eq 1 ]] || exit "$rc"
    done <<< "$dirs"
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
