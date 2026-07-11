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

@test "setup_hooks.sh: unknown option is rejected" {
  run "$SCRIPT" --bogus
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown option"* ]]
}

@test "setup_hooks.sh: --all without directory is rejected" {
  run "$SCRIPT" --all
  [ "$status" -ne 0 ]
  [[ "$output" == *"--all requires a directory"* ]]
}

@test "setup_hooks.sh: --all with non-existent directory is rejected" {
  run "$SCRIPT" --all "$TMPDIR_TEST/nope"
  [ "$status" -ne 0 ]
  [[ "$output" == *"directory not found"* ]]
}

# --- single-repo install (default mode) ---

@test "setup_hooks.sh: installs new pre-push symlink into a fresh repo" {
  local repo="$TMPDIR_TEST/repo"
  mkdir -p "$repo/.git/hooks"
  export GIT_STUB_GIT_DIR="$repo/.git"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"installed"* ]]
  [ -L "$repo/.git/hooks/pre-push" ]
  [ "$(readlink "$repo/.git/hooks/pre-push")" = "$HOOK_SOURCE" ]
}

@test "setup_hooks.sh: idempotent when symlink already points to HOOK_SOURCE" {
  local repo="$TMPDIR_TEST/repo"
  mkdir -p "$repo/.git/hooks"
  ln -s "$HOOK_SOURCE" "$repo/.git/hooks/pre-push"
  export GIT_STUB_GIT_DIR="$repo/.git"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"already installed"* ]]
}

@test "setup_hooks.sh: pre-existing non-symlink hook is skipped without --force" {
  local repo="$TMPDIR_TEST/repo"
  mkdir -p "$repo/.git/hooks"
  echo "#!/bin/sh" > "$repo/.git/hooks/pre-push"
  chmod +x "$repo/.git/hooks/pre-push"
  export GIT_STUB_GIT_DIR="$repo/.git"
  run "$SCRIPT"
  # install_hook returns 1 (skip); the wrapper treats rc=1 as non-fatal.
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipped"* ]]
  [ ! -L "$repo/.git/hooks/pre-push" ]
}

@test "setup_hooks.sh: --force overwrites existing hook" {
  local repo="$TMPDIR_TEST/repo"
  mkdir -p "$repo/.git/hooks"
  echo "#!/bin/sh" > "$repo/.git/hooks/pre-push"
  export GIT_STUB_GIT_DIR="$repo/.git"
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

@test "setup_hooks.sh: .git as file (worktree/submodule layout) is rejected" {
  local repo="$TMPDIR_TEST/repo"
  mkdir -p "$repo"
  echo "gitdir: /some/where" > "$repo/.git"
  export GIT_STUB_GIT_DIR="$repo/.git"
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"unsupported Git layout"* ]]
}

# --- legacy post-commit cleanup ---

@test "setup_hooks.sh: removes legacy post-commit symlink pointing to ayumy" {
  local repo="$TMPDIR_TEST/repo"
  mkdir -p "$repo/.git/hooks"
  ln -s "$LEGACY_HOOK_SOURCE" "$repo/.git/hooks/post-commit"
  export GIT_STUB_GIT_DIR="$repo/.git"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"removed legacy"* ]]
  [ ! -e "$repo/.git/hooks/post-commit" ]
}

@test "setup_hooks.sh: leaves unrelated post-commit symlink intact" {
  local repo="$TMPDIR_TEST/repo"
  mkdir -p "$repo/.git/hooks"
  ln -s "/some/unrelated/path" "$repo/.git/hooks/post-commit"
  export GIT_STUB_GIT_DIR="$repo/.git"
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" != *"removed legacy"* ]]
  [ -L "$repo/.git/hooks/post-commit" ]
  [ "$(readlink "$repo/.git/hooks/post-commit")" = "/some/unrelated/path" ]
}

# --- --all batch install ---

@test "setup_hooks.sh: --all installs hooks across multiple repos and summarizes" {
  local root="$TMPDIR_TEST/projects"
  mkdir -p "$root/repo1/.git" "$root/repo2/.git" "$root/repo3/.git"
  run "$SCRIPT" --all "$root"
  [ "$status" -eq 0 ]
  [[ "$output" == *"3 repos found, 3 installed"* ]]
  [ -L "$root/repo1/.git/hooks/pre-push" ]
  [ -L "$root/repo2/.git/hooks/pre-push" ]
  [ -L "$root/repo3/.git/hooks/pre-push" ]
}

@test "setup_hooks.sh: --all aggregates installed and skipped counts" {
  local root="$TMPDIR_TEST/projects"
  mkdir -p "$root/fresh/.git/hooks" "$root/blocked/.git/hooks"
  echo "#!/bin/sh" > "$root/blocked/.git/hooks/pre-push"
  run "$SCRIPT" --all "$root"
  [ "$status" -eq 0 ]
  [[ "$output" == *"2 repos found, 1 installed, 1 skipped"* ]]
  [ -L "$root/fresh/.git/hooks/pre-push" ]
  [ ! -L "$root/blocked/.git/hooks/pre-push" ]
}

@test "setup_hooks.sh: --all with no Git repos in directory is rejected" {
  local root="$TMPDIR_TEST/projects"
  mkdir -p "$root/justadir"
  run "$SCRIPT" --all "$root"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no Git repositories found"* ]]
}
