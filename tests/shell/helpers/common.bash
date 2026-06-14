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
