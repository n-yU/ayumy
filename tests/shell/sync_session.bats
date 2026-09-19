#!/usr/bin/env bats

load 'helpers/common'

SCRIPT="$AYUMY_REPO_ROOT/scripts/sync_session.sh"

setup() {
  setup_shell_test
  export HOME="$TMPDIR_TEST/home"
  PROJECTS_DIR="$HOME/.claude/projects"
  mkdir -p "$PROJECTS_DIR"
  export AYUMY_S3_BUCKET="test-bucket"
  unset AYUMY_LAMBDA_FUNCTION
}

teardown() {
  teardown_shell_test
}

# --- option parsing ---

@test "sync_session.sh: --help exits 0 with usage on stdout" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' --help >'$out' 2>'$err'"
  [ "$status" -eq 0 ]
  grep -q "Usage:" "$out"
  [ ! -s "$err" ]
}

@test "sync_session.sh: -h exits 0 with usage on stdout" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' -h >'$out' 2>'$err'"
  [ "$status" -eq 0 ]
  grep -q "Usage:" "$out"
  [ ! -s "$err" ]
}

@test "sync_session.sh: --project + --all conflict is rejected" {
  run "$SCRIPT" --project foo --all
  [ "$status" -ne 0 ]
  [[ "$output" == *"conflicting options"* ]]
}

@test "sync_session.sh: --all + --project conflict is rejected" {
  run "$SCRIPT" --all --project foo
  [ "$status" -ne 0 ]
  [[ "$output" == *"conflicting options"* ]]
}

@test "sync_session.sh: --date without --report is rejected" {
  run "$SCRIPT" --all --date 2026-06-15
  [ "$status" -ne 0 ]
  [[ "$output" == *"--date requires --report"* ]]
}

@test "sync_session.sh: --date with YYYY-MM-DD is accepted" {
  AYUMY_LAMBDA_FUNCTION="fn" run "$SCRIPT" --all --report --date 2026-06-15
  [ "$status" -eq 0 ]
}

@test "sync_session.sh: --date with YYYY-MM-DD..YYYY-MM-DD is accepted" {
  AYUMY_LAMBDA_FUNCTION="fn" run "$SCRIPT" --all --report --date 2026-06-01..2026-06-15
  [ "$status" -eq 0 ]
}

@test "sync_session.sh: --date with invalid format is rejected" {
  AYUMY_LAMBDA_FUNCTION="fn" run "$SCRIPT" --all --report --date not-a-date
  [ "$status" -ne 0 ]
  [[ "$output" == *"--date must be YYYY-MM-DD"* ]]
}

@test "sync_session.sh: --cwd + --all conflict is rejected" {
  run "$SCRIPT" --cwd /path/to/repo --all
  [ "$status" -ne 0 ]
  [[ "$output" == *"conflicting options"* ]]
}

@test "sync_session.sh: --cwd without a directory is rejected" {
  run "$SCRIPT" --cwd
  [ "$status" -ne 0 ]
  [[ "$output" == *"--cwd requires a directory"* ]]
}

@test "sync_session.sh: --project without a name is rejected" {
  run "$SCRIPT" --project
  [ "$status" -ne 0 ]
  [[ "$output" == *"--project requires a name"* ]]
}

@test "sync_session.sh: unknown option is rejected" {
  run "$SCRIPT" --bogus
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown option"* ]]
}

# --- AYUMY_S3_BUCKET normalization ---

@test "sync_session.sh: bucket name strips s3:// prefix" {
  make_project
  echo '{}' > "$proj_dir/sess1.jsonl"
  AYUMY_S3_BUCKET="s3://my-bkt" run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  grep -q 's3://my-bkt/claude-sessions/myproj/' "$AWS_STUB_LOG"
}

@test "sync_session.sh: bucket name strips trailing slashes" {
  make_project
  echo '{}' > "$proj_dir/sess1.jsonl"
  AYUMY_S3_BUCKET="my-bkt///" run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  grep -q 's3://my-bkt/claude-sessions/myproj/' "$AWS_STUB_LOG"
}

@test "sync_session.sh: path-like bucket name is rejected" {
  AYUMY_S3_BUCKET="bucket/sub" run "$SCRIPT" --all
  [ "$status" -ne 0 ]
  [[ "$output" == *"plain bucket name"* ]]
}

@test "sync_session.sh: empty bucket name is rejected" {
  AYUMY_S3_BUCKET="" run "$SCRIPT" --all
  [ "$status" -ne 0 ]
  [[ "$output" == *"AYUMY_S3_BUCKET is not set"* ]]
}

@test "sync_session.sh: bucket of only s3:// is rejected after stripping" {
  AYUMY_S3_BUCKET="s3://" run "$SCRIPT" --all
  [ "$status" -ne 0 ]
  [[ "$output" == *"plain bucket name"* ]]
}

# --- find_changed_sessions / sync_project return codes ---

@test "sync_session.sh: no marker uploads all JSONL files and writes marker" {
  make_project
  echo '{}' > "$proj_dir/a.jsonl"
  echo '{}' > "$proj_dir/b.jsonl"
  run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  local count
  count=$(grep -c "^aws s3 cp $proj_dir/" "$AWS_STUB_LOG" || true)
  [ "$count" -eq 2 ]
  [ -f "$proj_dir/.ayumy_last_sync" ]
}

@test "sync_session.sh: marker present uploads only newer JSONL" {
  make_project
  echo '{}' > "$proj_dir/old.jsonl"
  touch -t 202001010000 "$proj_dir/old.jsonl"
  touch -t 202401010000 "$proj_dir/.ayumy_last_sync"
  echo '{}' > "$proj_dir/new.jsonl"
  run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $proj_dir/new.jsonl " "$AWS_STUB_LOG"
  ! grep -q "^aws s3 cp $proj_dir/old.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: no changes logs 'no changes' and exits 0" {
  make_project
  echo '{}' > "$proj_dir/x.jsonl"
  touch -t 202001010000 "$proj_dir/x.jsonl"
  touch -t 202401010000 "$proj_dir/.ayumy_last_sync"
  run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  [[ "$output" == *"no changes"* ]]
  ! grep -q "^aws s3 cp " "$AWS_STUB_LOG"
}

@test "sync_session.sh: s3 cp failure surfaces as exit 2" {
  make_project
  echo '{}' > "$proj_dir/a.jsonl"
  AWS_STUB_S3_EXIT=1 run "$SCRIPT" --project myproj
  [ "$status" -eq 2 ]
  [[ "$output" == *"failed to upload"* ]]
}

@test "sync_session.sh: .ayumy_repo metadata is uploaded alongside JSONL when present" {
  make_project
  echo '{}' > "$proj_dir/a.jsonl"
  echo 'my-repo' > "$proj_dir/.ayumy_repo"
  run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $proj_dir/a.jsonl " "$AWS_STUB_LOG"
  grep -q "^aws s3 cp $proj_dir/.ayumy_repo " "$AWS_STUB_LOG"
}

@test "sync_session.sh: .ayumy_repo metadata is uploaded even without JSONL changes" {
  make_project
  echo '{}' > "$proj_dir/x.jsonl"
  touch -t 202001010000 "$proj_dir/x.jsonl"
  touch -t 202401010000 "$proj_dir/.ayumy_last_sync"
  echo 'my-repo' > "$proj_dir/.ayumy_repo"
  run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  [[ "$output" == *"no changes"* ]]
  grep -q "^aws s3 cp $proj_dir/.ayumy_repo " "$AWS_STUB_LOG"
}

@test "sync_session.sh: .ayumy_repo upload failure surfaces as exit 2" {
  make_project
  echo '{}' > "$proj_dir/a.jsonl"
  echo 'my-repo' > "$proj_dir/.ayumy_repo"
  AWS_STUB_S3_FAIL_PATTERN=".ayumy_repo" run "$SCRIPT" --project myproj
  [ "$status" -eq 2 ]
  [[ "$output" == *".ayumy_repo metadata"* ]]
}

# --- --cwd resolution ---

# Create a project dir whose session log records $2 as the working directory.
make_cwd_project() {
  make_project "$1"
  printf '{"type":"user","cwd":"%s"}\n' "$2" > "$proj_dir/sess.jsonl"
}

@test "sync_session.sh: --cwd uploads the project recording the directory" {
  make_cwd_project "-path-to-repo" "/path/to/repo"
  run "$SCRIPT" --cwd /path/to/repo
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd resolves a path whose name Claude Code rewrites" {
  make_cwd_project "-path-to-repo-worktrees-topic" "/path/to/repo.worktrees/topic"
  run "$SCRIPT" --cwd /path/to/repo.worktrees/topic
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo-worktrees-topic/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd ignores a project whose later entries visit the directory" {
  make_project "-path-to-other"
  printf '{"type":"user","cwd":"/path/to/other"}\n{"type":"user","cwd":"/path/to/repo.worktrees/topic"}\n' > "$proj_dir/sess.jsonl"
  make_cwd_project "-path-to-repo-worktrees-topic" "/path/to/repo.worktrees/topic"
  run "$SCRIPT" --cwd /path/to/repo.worktrees/topic
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo-worktrees-topic/sess.jsonl " "$AWS_STUB_LOG"
  ! grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-other/" "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd resolves when the first entry records no cwd" {
  make_project "-path-to-repo-worktrees-topic"
  printf '{"type":"queue-operation"}\n{"type":"user","cwd":"/path/to/repo.worktrees/topic"}\n' > "$proj_dir/sess.jsonl"
  run "$SCRIPT" --cwd /path/to/repo.worktrees/topic
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo-worktrees-topic/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd uploads every project recording the same cwd" {
  make_cwd_project "derived-a" "/path/to/repo.worktrees/topic"
  make_cwd_project "derived-b" "/path/to/repo.worktrees/topic"
  run "$SCRIPT" --cwd /path/to/repo.worktrees/topic
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/derived-a/sess.jsonl " "$AWS_STUB_LOG"
  grep -q "^aws s3 cp $PROJECTS_DIR/derived-b/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd resolves a relative directory to its absolute path" {
  mkdir -p "$TMPDIR_TEST/work.dir"
  local abs
  abs="$(cd "$TMPDIR_TEST/work.dir" && pwd)"
  make_cwd_project "derived-name" "$abs"
  cd "$TMPDIR_TEST"
  run "$SCRIPT" --cwd work.dir
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/derived-name/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd with an unresolvable relative path is rejected" {
  run "$SCRIPT" --cwd no/such/dir
  [ "$status" -ne 0 ]
  [[ "$output" == *"absolute path"* ]]
}

@test "sync_session.sh: --cwd / does not resolve the projects root" {
  make_cwd_project "-path-to-repo" "/path/to/repo"
  run "$SCRIPT" --cwd /
  [ "$status" -eq 0 ]
  [[ "$output" == *"no sessions recorded"* ]]
  ! grep -q "^aws s3 cp " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd ignores repeated trailing slashes" {
  make_cwd_project "-path-to-repo-worktrees-topic" "/path/to/repo.worktrees/topic"
  run "$SCRIPT" --cwd /path/to/repo.worktrees/topic///
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo-worktrees-topic/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd ignores a project recording a different path under the same name" {
  make_cwd_project "-path-to-foo-bar" "/path/to/foo.bar"
  run "$SCRIPT" --cwd /path/to/foo-bar
  [ "$status" -eq 0 ]
  [[ "$output" == *"no sessions recorded"* ]]
  ! grep -q "^aws s3 cp " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd uploads projects beyond the one named after the directory" {
  make_cwd_project "-path-to-repo" "/path/to/repo"
  make_cwd_project "-path-to-repo-legacy" "/path/to/repo"
  run "$SCRIPT" --cwd /path/to/repo
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo/sess.jsonl " "$AWS_STUB_LOG"
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo-legacy/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd ignores a trailing slash on the directory" {
  make_cwd_project "-path-to-repo-worktrees-topic" "/path/to/repo.worktrees/topic"
  run "$SCRIPT" --cwd /path/to/repo.worktrees/topic/
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo-worktrees-topic/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: --repo records the name in every resolved project" {
  make_cwd_project "derived-a" "/path/to/repo.worktrees/topic"
  make_cwd_project "derived-b" "/path/to/repo.worktrees/topic"
  run "$SCRIPT" --cwd /path/to/repo.worktrees/topic --repo my-repo
  [ "$status" -eq 0 ]
  [ "$(cat "$PROJECTS_DIR/derived-a/.ayumy_repo")" = "my-repo" ]
  [ "$(cat "$PROJECTS_DIR/derived-b/.ayumy_repo")" = "my-repo" ]
}

@test "sync_session.sh: --repo without --cwd is rejected" {
  run "$SCRIPT" --all --repo my-repo
  [ "$status" -ne 0 ]
  [[ "$output" == *"--repo requires --cwd"* ]]
}

@test "sync_session.sh: --repo without a name is rejected" {
  run "$SCRIPT" --cwd /path/to/repo --repo
  [ "$status" -ne 0 ]
  [[ "$output" == *"--repo requires a name"* ]]
}

@test "sync_session.sh: no args resolves the project from the git root" {
  make_cwd_project "-path-to-repo-worktrees-topic" "/path/to/repo.worktrees/topic"
  GIT_STUB_TOPLEVEL="/path/to/repo.worktrees/topic" run "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $PROJECTS_DIR/-path-to-repo-worktrees-topic/sess.jsonl " "$AWS_STUB_LOG"
}

@test "sync_session.sh: no args outside a git repository is rejected" {
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not in a git repository"* ]]
}

@test "sync_session.sh: --cwd without a projects directory uploads nothing" {
  rm -rf "$PROJECTS_DIR"
  run "$SCRIPT" --cwd /path/to/repo
  [ "$status" -eq 0 ]
  [[ "$output" == *"no sessions recorded"* ]]
}

@test "sync_session.sh: --all without a projects directory is rejected" {
  rm -rf "$PROJECTS_DIR"
  run "$SCRIPT" --all
  [ "$status" -ne 0 ]
  [[ "$output" == *"does not exist"* ]]
}

@test "sync_session.sh: --cwd without sessions succeeds before the bucket check" {
  unset AYUMY_S3_BUCKET
  run "$SCRIPT" --cwd /path/to/repo
  [ "$status" -eq 0 ]
  [[ "$output" == *"no sessions recorded"* ]]
}

@test "sync_session.sh: --cwd with sessions still requires the bucket" {
  make_cwd_project "-path-to-repo" "/path/to/repo"
  unset AYUMY_S3_BUCKET
  run "$SCRIPT" --cwd /path/to/repo
  [ "$status" -ne 0 ]
  [[ "$output" == *"AYUMY_S3_BUCKET is not set"* ]]
}

@test "sync_session.sh: --cwd without sessions still reports when requested" {
  AYUMY_LAMBDA_FUNCTION="fn" run "$SCRIPT" --cwd /path/to/repo --report
  [ "$status" -eq 0 ]
  grep -q 'aws lambda invoke' "$AWS_STUB_LOG"
}

@test "sync_session.sh: --cwd without a matching session uploads nothing" {
  make_cwd_project "-other-repo" "/other/repo"
  run "$SCRIPT" --cwd /path/to/repo
  [ "$status" -eq 0 ]
  [[ "$output" == *"no sessions recorded"* ]]
  ! grep -q "^aws s3 cp " "$AWS_STUB_LOG"
}

# --- --report path ---

@test "sync_session.sh: --report invokes Lambda with manual source" {
  AYUMY_LAMBDA_FUNCTION="my-fn" run "$SCRIPT" --all --report
  [ "$status" -eq 0 ]
  grep -q 'aws lambda invoke' "$AWS_STUB_LOG"
  grep -q '"source": "manual"' "$AWS_STUB_LOG"
}

@test "sync_session.sh: --report with --date includes target_date in payload" {
  AYUMY_LAMBDA_FUNCTION="my-fn" run "$SCRIPT" --all --report --date 2026-06-15
  [ "$status" -eq 0 ]
  grep -q '"target_date": "2026-06-15"' "$AWS_STUB_LOG"
}

@test "sync_session.sh: --report passes function name to lambda invoke" {
  AYUMY_LAMBDA_FUNCTION="my-fn" run "$SCRIPT" --all --report
  [ "$status" -eq 0 ]
  grep -q -- '--function-name my-fn' "$AWS_STUB_LOG"
}

@test "sync_session.sh: --report without AYUMY_LAMBDA_FUNCTION is rejected" {
  run "$SCRIPT" --all --report
  [ "$status" -ne 0 ]
  [[ "$output" == *"AYUMY_LAMBDA_FUNCTION is not set"* ]]
}
