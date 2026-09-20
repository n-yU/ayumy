_TESTS_HELPERS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AYUMY_REPO_ROOT="$(cd "$_TESTS_HELPERS_DIR/../../.." && pwd)"
export STUB_DIR="$_TESTS_HELPERS_DIR/stubs"

# Create a per-test tmpdir, init stub log files, and prepend stubs to PATH.
setup_shell_test() {
  TMPDIR_TEST="$(mktemp -d "${BATS_TMPDIR:-/tmp}/ayumy-bats-XXXXXX")"
  AWS_STUB_LOG="$TMPDIR_TEST/aws.log"
  GIT_STUB_LOG="$TMPDIR_TEST/git.log"
  : > "$AWS_STUB_LOG"
  : > "$GIT_STUB_LOG"
  export TMPDIR_TEST AWS_STUB_LOG GIT_STUB_LOG
  export PATH="$STUB_DIR:$PATH"
}

teardown_shell_test() {
  if [[ -n "${TMPDIR_TEST:-}" && -d "$TMPDIR_TEST" ]]; then
    rm -rf "$TMPDIR_TEST"
  fi
}

# Create a session project dir under PROJECTS_DIR and set proj_dir to its path.
# PROJECTS_DIR comes from the calling suite's setup, so fail rather than build a rootless path.
# A repository name is recorded because a project without one is never transferred.
make_project() {
  proj_dir="${PROJECTS_DIR:?PROJECTS_DIR must be set by setup}/${1:-myproj}"
  mkdir -p "$proj_dir"
  echo 'stub-repo' > "$proj_dir/.ayumy_repo"
}

# Create a repo with an empty hooks dir under TMPDIR_TEST, point the git stub at it,
# and set repo to its path.
make_git_repo() {
  repo="$TMPDIR_TEST/${1:-repo}"
  mkdir -p "$repo/.git/hooks"
  export GIT_STUB_HOOKS_DIR="$repo/.git/hooks"
}

# Dump status / output to stderr; surfaces on bats failure.
debug_run() {
  echo "status: ${status:-<unset>}" >&2
  echo "output:" >&2
  echo "${output:-}" >&2
}

aws_stub_log() {
  cat "$AWS_STUB_LOG"
}

git_stub_log() {
  cat "$GIT_STUB_LOG"
}
