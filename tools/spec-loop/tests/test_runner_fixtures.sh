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
trap 'rm -rf "$TMP"' EXIT

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

echo ""
echo "──────────────────────────────"
echo "PASS: $PASS   FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
