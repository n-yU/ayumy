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
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
  echo '{}' > "$proj_dir/sess1.jsonl"
  AYUMY_S3_BUCKET="s3://my-bkt" run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  grep -q 's3://my-bkt/claude-sessions/myproj/' "$AWS_STUB_LOG"
}

@test "sync_session.sh: bucket name strips trailing slashes" {
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
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
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
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
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
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
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
  echo '{}' > "$proj_dir/x.jsonl"
  touch -t 202001010000 "$proj_dir/x.jsonl"
  touch -t 202401010000 "$proj_dir/.ayumy_last_sync"
  run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  [[ "$output" == *"no changes"* ]]
  ! grep -q "^aws s3 cp " "$AWS_STUB_LOG"
}

@test "sync_session.sh: s3 cp failure surfaces as exit 2" {
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
  echo '{}' > "$proj_dir/a.jsonl"
  AWS_STUB_S3_EXIT=1 run "$SCRIPT" --project myproj
  [ "$status" -eq 2 ]
  [[ "$output" == *"failed to upload"* ]]
}

@test "sync_session.sh: .ayumy_repo metadata is uploaded alongside JSONL when present" {
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
  echo '{}' > "$proj_dir/a.jsonl"
  echo 'my-repo' > "$proj_dir/.ayumy_repo"
  run "$SCRIPT" --project myproj
  [ "$status" -eq 0 ]
  grep -q "^aws s3 cp $proj_dir/a.jsonl " "$AWS_STUB_LOG"
  grep -q "^aws s3 cp $proj_dir/.ayumy_repo " "$AWS_STUB_LOG"
}

@test "sync_session.sh: .ayumy_repo upload failure surfaces as exit 2" {
  local proj_dir="$PROJECTS_DIR/myproj"
  mkdir -p "$proj_dir"
  echo '{}' > "$proj_dir/a.jsonl"
  echo 'my-repo' > "$proj_dir/.ayumy_repo"
  AWS_STUB_S3_FAIL_PATTERN=".ayumy_repo" run "$SCRIPT" --project myproj
  [ "$status" -eq 2 ]
  [[ "$output" == *".ayumy_repo metadata"* ]]
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
