#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#   https://www.apache.org/licenses/LICENSE-2.0
#
# Deterministic fixture tests for tools/spec-loop/lib.sh. These pin the
# runner's prompt assembly and harness argv construction WITHOUT launching a
# real agent, by substituting a fake agent that records the argv it was given.
#
# Run:  bash tools/spec-loop/tests/test_runner_fixtures.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tools/spec-loop/lib.sh
. "$HERE/../lib.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/spec-loop-test.XXXXXX")"
# Clean up on interrupt and at the end of the script. Not an EXIT trap: bash
# runs an inherited EXIT trap inside a forked child that dies before its exec,
# which would delete $TMP from under the still-running test.
trap 'rm -rf "$TMP"; exit 130' INT TERM

PASS=0
FAIL=0
check() { # check <description> <expected-substring> <actual>
    if printf '%s' "$3" | grep -qF -- "$2"; then
        PASS=$((PASS+1)); printf '  ok   %s\n' "$1"
    else
        FAIL=$((FAIL+1)); printf '  FAIL %s\n     want substring: %s\n     got: %s\n' "$1" "$2" "$3"
    fi
}
check_absent() { # check_absent <description> <forbidden-substring> <actual>
    if printf '%s' "$3" | grep -qF -- "$2"; then
        FAIL=$((FAIL+1)); printf '  FAIL %s\n     unexpected substring: %s\n' "$1" "$2"
    else
        PASS=$((PASS+1)); printf '  ok   %s\n' "$1"
    fi
}

# ---- prompt assembly -------------------------------------------------
echo "spec_loop_assemble_prompt_file:"
printf 'BASE PROMPT\n'   > "$TMP/prompt.md"
printf 'SNAPSHOT CTX\n'  > "$TMP/snap.md"
printf 'PR CTX\n'        > "$TMP/pr.md"
printf 'BRANCH CTX\n'    > "$TMP/branch.md"

spec_loop_assemble_prompt_file "$TMP/prompt.md" "$TMP/out_build.md" build \
    "$TMP/snap.md" "$TMP/pr.md" "$TMP/branch.md"
built="$(cat "$TMP/out_build.md")"
check "build includes base prompt"    "BASE PROMPT"  "$built"
check "build includes snapshot"       "SNAPSHOT CTX" "$built"
check "build includes PR context"     "PR CTX"       "$built"
check "build includes branch context" "BRANCH CTX"   "$built"

spec_loop_assemble_prompt_file "$TMP/prompt.md" "$TMP/out_update.md" update \
    "$TMP/snap.md" "$TMP/pr.md" "$TMP/branch.md"
upd="$(cat "$TMP/out_update.md")"
check        "update includes branch context" "BRANCH CTX" "$upd"
check_absent "update omits PR context"        "PR CTX"     "$upd"

# ---- harness argv construction ---------------------------------------
# A fake agent that records its argv, so we assert flags without a real model.
echo "spec_loop_launch_agent:"
FAKE="$TMP/fake-agent"
cat > "$FAKE" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_ARGV_OUT"
EOF
chmod +x "$FAKE"
printf 'THE PROMPT\n' > "$TMP/p.md"

run_harness() { # run_harness <harness> <model> <output_format>
    export FAKE_ARGV_OUT="$TMP/argv-$1.txt"
    spec_loop_launch_agent "$1" "$FAKE" "$TMP" "$TMP/p.md" "$2" "$3"
    wait "$SPEC_LOOP_AGENT_PID" 2>/dev/null
    cat "$FAKE_ARGV_OUT"
}

claude_argv="$(run_harness claude sonnet text)"
check        "claude uses -p"                      "-p"                            "$claude_argv"
check        "claude skips permissions"            "--dangerously-skip-permissions" "$claude_argv"
check        "claude hard-denies git push"         "Bash(git push:*)"              "$claude_argv"
check        "claude hard-denies gh"               "Bash(gh:*)"                    "$claude_argv"
check        "claude passes model"                 "sonnet"                        "$claude_argv"
check_absent "claude text mode omits --verbose"    "--verbose"                     "$claude_argv"

codex_argv="$(run_harness codex '' text)"
check "codex uses exec"                    "exec"                                        "$codex_argv"
check "codex bypasses approvals/sandbox"   "--dangerously-bypass-approvals-and-sandbox"  "$codex_argv"

gemini_argv="$(run_harness gemini '' text)"
check "gemini uses --yolo"    "--yolo"    "$gemini_argv"
check "gemini uses --prompt"  "--prompt"  "$gemini_argv"

opencode_argv="$(run_harness opencode anthropic/claude-sonnet-4-5 text)"
check "opencode uses run"     "run"                          "$opencode_argv"
check "opencode uses --auto"  "--auto"                       "$opencode_argv"
check "opencode passes model" "anthropic/claude-sonnet-4-5"  "$opencode_argv"

# ---- single-flight lock ----------------------------------------------
echo "spec_loop_lock_acquire / spec_loop_lock_release:"
LOCK="$TMP/spec-loop.lock"

# A live holder: a background sleep we control.
sleep 300 & LIVE_PID=$!
# A dead holder: a child that has already exited and been reaped.
( exit 0 ) & DEAD_PID=$!
wait "$DEAD_PID" 2>/dev/null

first_out="$(spec_loop_lock_acquire "$LOCK" "$LIVE_PID")"; first_rc=$?
check "fresh acquire succeeds (rc=0)"          "0"              "$first_rc"
check "fresh acquire stamps the holder pid"    "pid=$LIVE_PID"  "$(cat "$LOCK")"
check_absent "fresh acquire is silent"         "reclaimed"      "$first_out"

second_out="$(spec_loop_lock_acquire "$LOCK" "$$")"; second_rc=$?
check "live lock refuses a second run (rc=1)"  "1"                            "$second_rc"
check "live lock reports already_running"      "already_running (pid=$LIVE_PID)" "$second_out"
check "live lock file is left untouched"       "pid=$LIVE_PID"                "$(cat "$LOCK")"

spec_loop_lock_release "$LOCK" "$$"
check "release by a non-holder leaves the lock" "pid=$LIVE_PID" "$(cat "$LOCK")"

kill "$LIVE_PID" 2>/dev/null; wait "$LIVE_PID" 2>/dev/null
# LIVE_PID is now dead too: the lock it left behind is stale.
stale_out="$(spec_loop_lock_acquire "$LOCK" "$$")"; stale_rc=$?
check "stale lock is reclaimed (rc=0)"         "0"                               "$stale_rc"
check "stale reclaim names the dead pid"       "reclaimed stale lock (pid=$LIVE_PID)" "$stale_out"
check "stale reclaim stamps the new holder"    "pid=$$"                          "$(cat "$LOCK")"

spec_loop_lock_release "$LOCK" "$$"
if [ -f "$LOCK" ]; then
    FAIL=$((FAIL+1)); printf '  FAIL release by the holder removes the lock\n'
else
    PASS=$((PASS+1)); printf '  ok   release by the holder removes the lock\n'
fi

printf 'garbage\n' > "$LOCK"
corrupt_out="$(spec_loop_lock_acquire "$LOCK" "$$")"; corrupt_rc=$?
check "corrupt lock is treated as stale (rc=0)" "0"                                "$corrupt_rc"
check "corrupt lock reclaim says so"            "reclaimed stale lock (pid=unknown)" "$corrupt_out"
check "corrupt lock is replaced by the holder"  "pid=$$"                           "$(cat "$LOCK")"
spec_loop_lock_release "$LOCK" "$$"

# Dead-pid path with a pid we know is dead (DEAD_PID was reaped above).
printf 'pid=%s\n' "$DEAD_PID" > "$LOCK"
dead_out="$(spec_loop_lock_acquire "$LOCK" "$$")"; dead_rc=$?
check "dead-pid lock is reclaimed (rc=0)"      "0"                                   "$dead_rc"
check "dead-pid reclaim names it"              "reclaimed stale lock (pid=$DEAD_PID)" "$dead_out"
spec_loop_lock_release "$LOCK" "$$"

echo ""
echo "──────────────────────────────"
echo "PASS: $PASS   FAIL: $FAIL"
rm -rf "$TMP"
[ "$FAIL" -eq 0 ]
