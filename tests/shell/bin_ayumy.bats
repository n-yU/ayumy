#!/usr/bin/env bats

load 'helpers/common'

SCRIPT="$AYUMY_REPO_ROOT/bin/ayumy"

setup() {
  setup_shell_test
}

teardown() {
  teardown_shell_test
}

# --- top level help ---

@test "bin/ayumy: --help exits 0 with usage on stdout" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' --help >'$out' 2>'$err'"
  [ "$status" -eq 0 ]
  grep -q "Usage: ayumy" "$out"
  [ ! -s "$err" ]
}

@test "bin/ayumy: -h exits 0 with usage on stdout" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' -h >'$out' 2>'$err'"
  [ "$status" -eq 0 ]
  grep -q "Usage: ayumy" "$out"
  [ ! -s "$err" ]
}

# --- error paths ---

@test "bin/ayumy: no-arg exits 1 with usage on stderr" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' >'$out' 2>'$err'"
  [ "$status" -eq 1 ]
  grep -q "Usage: ayumy" "$err"
  [ ! -s "$out" ]
}

@test "bin/ayumy: unknown command exits 1 with error and usage on stderr" {
  local out="$TMPDIR_TEST/out" err="$TMPDIR_TEST/err"
  run bash -c "'$SCRIPT' bogus >'$out' 2>'$err'"
  [ "$status" -eq 1 ]
  grep -q "unknown command 'bogus'" "$err"
  grep -q "Usage: ayumy" "$err"
  [ ! -s "$out" ]
}
