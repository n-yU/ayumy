#!/usr/bin/env bats

load 'helpers/common'

SCRIPT="$AYUMY_REPO_ROOT/scripts/setup_hooks.sh"
HOOK_SOURCE="$AYUMY_REPO_ROOT/hooks/pre-push"
LEGACY_HOOK_SOURCE="$AYUMY_REPO_ROOT/hooks/post-commit"

setup() {
  setup_shell_test
}

teardown() {
  teardown_shell_test
}

# --- option parsing ---

@test "setup_hooks.sh: --help exits 0 with usage on stdout" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' --help >'$out' 2>'$err'"
  [ "$status" -eq 0 ]
  grep -q "Usage:" "$out"
  [ ! -s "$err" ]
}

@test "setup_hooks.sh: -h exits 0 with usage on stdout" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' -h >'$out' 2>'$err'"
  [ "$status" -eq 0 ]
  grep -q "Usage:" "$out"
  [ ! -s "$err" ]
}

@test "setup_hooks.sh: --help succeeds even when the hook source is missing" {
  local fake_root="$TMPDIR_TEST/fake-ayumy"
  mkdir -p "$fake_root/scripts"
  cp "$SCRIPT" "$fake_root/scripts/setup_hooks.sh"

  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$fake_root/scripts/setup_hooks.sh' --help >'$out' 2>'$err'"
  [ "$status" -eq 0 ]
  grep -q "Usage:" "$out"
  [ ! -s "$err" ]
}

@test "setup_hooks.sh: unknown option is rejected" {
  run "$SCRIPT" --bogus
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown option"* ]]
}

# --- single-repo install ---

@test "setup_hooks.sh: installs new pre-push symlink into a fresh repo" {
  make_git_repo
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"installed"* ]]
  [ -L "$repo/.git/hooks/pre-push" ]
  [ "$(readlink "$repo/.git/hooks/pre-push")" = "$HOOK_SOURCE" ]
}

@test "setup_hooks.sh: idempotent when symlink already points to HOOK_SOURCE" {
  make_git_repo
  ln -s "$HOOK_SOURCE" "$repo/.git/hooks/pre-push"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"already installed"* ]]
}

@test "setup_hooks.sh: pre-existing non-symlink hook is skipped without --force" {
  make_git_repo
  echo "#!/bin/sh" > "$repo/.git/hooks/pre-push"
  chmod +x "$repo/.git/hooks/pre-push"
  run "$SCRIPT"
  # install_hook returns 1 (skip); the wrapper treats rc=1 as non-fatal.
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipped"* ]]
  [ ! -L "$repo/.git/hooks/pre-push" ]
}

@test "setup_hooks.sh: --force overwrites existing hook" {
  make_git_repo
  echo "#!/bin/sh" > "$repo/.git/hooks/pre-push"
  run "$SCRIPT" --force
  [ "$status" -eq 0 ]
  [ -L "$repo/.git/hooks/pre-push" ]
  [ "$(readlink "$repo/.git/hooks/pre-push")" = "$HOOK_SOURCE" ]
}

@test "setup_hooks.sh: not in a Git repository is rejected" {
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not a Git repository"* ]]
}

@test "setup_hooks.sh: installs into the hooks dir Git reports, not the worktree gitdir" {
  local common="$TMPDIR_TEST/main/.git"
  mkdir -p "$common/hooks" "$common/worktrees/wt/hooks"
  export GIT_STUB_HOOKS_DIR="$common/hooks"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [ -L "$common/hooks/pre-push" ]
  [ ! -e "$common/worktrees/wt/hooks/pre-push" ]
}

# --- legacy post-commit cleanup ---

@test "setup_hooks.sh: removes legacy post-commit symlink pointing to ayumy" {
  make_git_repo
  ln -s "$LEGACY_HOOK_SOURCE" "$repo/.git/hooks/post-commit"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed legacy"* ]]
  [ ! -e "$repo/.git/hooks/post-commit" ]
}

@test "setup_hooks.sh: leaves unrelated post-commit symlink intact" {
  make_git_repo
  ln -s "/some/unrelated/path" "$repo/.git/hooks/post-commit"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" != *"removed legacy"* ]]
  [ -L "$repo/.git/hooks/post-commit" ]
  [ "$(readlink "$repo/.git/hooks/post-commit")" = "/some/unrelated/path" ]
}
