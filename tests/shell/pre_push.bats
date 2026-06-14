#!/usr/bin/env bats

load 'helpers/common'

setup() {
  setup_shell_test

  FAKE_AYUMY="$TMPDIR_TEST/fake-ayumy"
  mkdir -p "$FAKE_AYUMY/hooks" "$FAKE_AYUMY/scripts"
  cp "$AYUMY_REPO_ROOT/hooks/pre-push" "$FAKE_AYUMY/hooks/pre-push"
  chmod +x "$FAKE_AYUMY/hooks/pre-push"
  HOOK="$FAKE_AYUMY/hooks/pre-push"

  SYNC_LOG="$TMPDIR_TEST/sync.log"
  cat > "$FAKE_AYUMY/scripts/sync_session.sh" <<EOF
#!/usr/bin/env bash
echo "sync_session.sh \$*" >> "$SYNC_LOG"
exit "\${SYNC_STUB_EXIT:-0}"
EOF
  chmod +x "$FAKE_AYUMY/scripts/sync_session.sh"

  export HOME="$TMPDIR_TEST/home"
  PROJECTS_BASE="$HOME/.claude/projects"
  mkdir -p "$PROJECTS_BASE"

  REPO_ROOT="/path/to/repo"
  PROJECT_NAME="$(echo "$REPO_ROOT" | sed 's|/|-|g')"
  PROJECT_DIR="$PROJECTS_BASE/$PROJECT_NAME"
  export GIT_STUB_TOPLEVEL="$REPO_ROOT"
}

teardown() {
  teardown_shell_test
}

@test "pre-push: resolves AYUMY_ROOT through symlink and invokes sync_session.sh" {
  local hook_link="$TMPDIR_TEST/repo-hooks/pre-push"
  mkdir -p "$(dirname "$hook_link")"
  ln -s "$HOOK" "$hook_link"
  mkdir -p "$PROJECT_DIR"
  run "$hook_link" origin "git@github.com:foo/bar.git"
  [ "$status" -eq 0 ]
  grep -q -- "--project $PROJECT_NAME" "$SYNC_LOG"
}

@test "pre-push: missing sync_session.sh exits 1" {
  rm "$FAKE_AYUMY/scripts/sync_session.sh"
  run "$HOOK" origin "git@github.com:foo/bar.git"
  [ "$status" -ne 0 ]
  [[ "$output" == *"sync script not found"* ]]
}

@test "pre-push: git root unresolvable exits 1" {
  unset GIT_STUB_TOPLEVEL
  run "$HOOK" origin "git@github.com:foo/bar.git"
  [ "$status" -ne 0 ]
  [[ "$output" == *"could not resolve git root"* ]]
}

@test "pre-push: project dir missing exits 0 to pass push through" {
  run "$HOOK" origin "git@github.com:foo/bar.git"
  [ "$status" -eq 0 ]
  [ ! -s "$SYNC_LOG" ]
}

@test "pre-push: writes .ayumy_repo from \$2 (remote URL)" {
  mkdir -p "$PROJECT_DIR"
  run "$HOOK" origin "git@github.com:my-org/my-repo.git"
  [ "$status" -eq 0 ]
  [ -f "$PROJECT_DIR/.ayumy_repo" ]
  [ "$(cat "$PROJECT_DIR/.ayumy_repo")" = "my-repo" ]
}

@test "pre-push: falls back to git remote get-url when \$2 is empty" {
  mkdir -p "$PROJECT_DIR"
  export GIT_STUB_REMOTE_URL="https://github.com/owner/fallback.git"
  run "$HOOK" origin
  [ "$status" -eq 0 ]
  [ "$(cat "$PROJECT_DIR/.ayumy_repo")" = "fallback" ]
}

@test "pre-push: sync_session.sh failure exits 1 with diagnostic" {
  mkdir -p "$PROJECT_DIR"
  SYNC_STUB_EXIT=1 run "$HOOK" origin "git@github.com:foo/bar.git"
  [ "$status" -eq 1 ]
  [[ "$output" == *"session upload failed; aborting push"* ]]
}
