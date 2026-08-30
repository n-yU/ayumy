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

# --- Notion icon setup ---

# Run the script from a throwaway ayumy root so the tests never append to the tracked config.
# Sets fake_script and fake_config.
make_fake_root() {
  local fake_root="$TMPDIR_TEST/fake-ayumy"
  mkdir -p "$fake_root/scripts" "$fake_root/hooks" "$fake_root/lambda/config"
  cp "$SCRIPT" "$fake_root/scripts/setup_hooks.sh"
  printf '#!/usr/bin/env bash\n' > "$fake_root/hooks/pre-push"
  chmod +x "$fake_root/hooks/pre-push"
  fake_script="$fake_root/scripts/setup_hooks.sh"
  fake_config="$fake_root/lambda/config/config.yml"
  cat > "$fake_config" <<'YAML'
notion:
  default_icon: {name: document, color: gray}
  repository_icons:
    already-set: {name: walk, color: blue}
YAML
}

# Arguments: $@ = answers fed to the prompts, one per line
run_setup_with_answers() {
  local answers=""
  local line
  for line in "$@"; do
    answers+="$line"$'\n'
  done
  run bash -c "printf '%s' \"\$1\" | '$fake_script'" _ "$answers"
}

@test "setup_hooks.sh: records the answered icon for a repository with no entry" {
  make_git_repo
  make_fake_root
  export GIT_STUB_REMOTE_URL="git@github.com:n-yU/fresh-repo.git"

  run_setup_with_answers "person walking" "green"
  [ "$status" -eq 0 ]
  grep -q "^    fresh-repo: {name: person walking, color: green}$" "$fake_config"
}

@test "setup_hooks.sh: defaults the icon color when the answer is blank" {
  make_git_repo
  make_fake_root
  export GIT_STUB_REMOTE_URL="git@github.com:n-yU/fresh-repo.git"

  run_setup_with_answers "rocket" ""
  [ "$status" -eq 0 ]
  grep -q "^    fresh-repo: {name: rocket, color: gray}$" "$fake_config"
}

@test "setup_hooks.sh: leaves an already configured repository untouched" {
  make_git_repo
  make_fake_root
  export GIT_STUB_REMOTE_URL="git@github.com:n-yU/already-set.git"
  local before
  before="$(cat "$fake_config")"

  run_setup_with_answers
  [ "$status" -eq 0 ]
  [[ "$output" == *"already configured"* ]]
  [ "$(cat "$fake_config")" = "$before" ]
}

@test "setup_hooks.sh: rejects an empty icon name" {
  make_git_repo
  make_fake_root
  export GIT_STUB_REMOTE_URL="git@github.com:n-yU/fresh-repo.git"

  run_setup_with_answers "" ""
  [ "$status" -ne 0 ]
  [[ "$output" == *"icon name is required"* ]]
  ! grep -q "fresh-repo" "$fake_config"
}

@test "setup_hooks.sh: rejects an unknown icon color" {
  make_git_repo
  make_fake_root
  export GIT_STUB_REMOTE_URL="git@github.com:n-yU/fresh-repo.git"

  run_setup_with_answers "rocket" "chartreuse"
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown color"* ]]
  ! grep -q "fresh-repo" "$fake_config"
}

@test "setup_hooks.sh: skips icon setup when the repository has no origin remote" {
  make_git_repo
  make_fake_root
  local before
  before="$(cat "$fake_config")"

  run_setup_with_answers
  [ "$status" -eq 0 ]
  [[ "$output" == *"no origin remote"* ]]
  [ "$(cat "$fake_config")" = "$before" ]
}
