#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#   https://www.apache.org/licenses/LICENSE-2.0
#
# Spec-driven build loop for Jumar.
#
# A small loop in the general "Ralph" style (run a fresh agent context against
# a fixed prompt, repeat), adapted from the airflow-steward spec-loop to a
# single-developer Python project:
#
#   * FOUR beats, ONE mechanism:
#       ./loop.sh                 build, unlimited iterations
#       ./loop.sh 10              build, max 10 iterations
#       ./loop.sh build [N]       build, max N iterations (N=0/omitted = unlimited)
#       ./loop.sh plan [N]        gap-analysis only, rewrites the plan (default 1; N=0 unlimited)
#       ./loop.sh update [N]      back-fill specs from code contributed the normal way (default 1)
#       ./loop.sh consolidate [N] shrink the plan when it grows too long (default 1)
#       ./loop.sh -h | --help     usage
#   * BRANCH PER WORK ITEM. build/update fork a branch off the integration base
#     (default: main); the beat implements exactly one work item there.
#     One work item = one branch = one PR.
#   * NEVER pushes, NEVER opens a PR. Each beat ends at a LOCAL commit and
#     prints the human-run `git push` + `gh pr create --web` commands.
#   * NO REDOING BUILT WORK. Because the loop never pushes, a built item exists
#     only as a LOCAL BRANCH with no open PR. Each iteration feeds the agent
#     BOTH the open PRs and the local work-item branches as in-flight work, so
#     it never re-picks and rebuilds the same top item forever.
#
# SECURITY — read before running:
#   Runs the agent in its unattended / auto-approval mode (Claude's
#   `--dangerously-skip-permissions`, Codex's
#   `--dangerously-bypass-approvals-and-sandbox`, Cursor's `--force`, Gemini's
#   `--yolo`). That bypasses the AGENT permission layer, NOT an external OS
#   sandbox. Launch it inside a sandbox with NO push/write credentials in the
#   environment. For Claude the loop also hard-denies `git push` and `gh` as
#   defence in depth (see lib.sh).
#
# Stop gracefully: Ctrl+C, or `touch STOP` (exits after the current iteration).
#
# Env overrides:
#   SPEC_LOOP_BASE     branch to fork work items from (default: main)
#   SPEC_LOOP_AGENT    agent CLI to run (default: claude; codex / cursor /
#                      gemini / opencode / kiro also supported)
#   SPEC_LOOP_HARNESS  invocation convention when the CLI name doesn't imply it
#   SPEC_LOOP_MODEL    model passed to the agent CLI (default: sonnet for Claude)
#   SPEC_LOOP_PR_LIMIT open PRs to list for duplicate-work checks (default: 100)
#   SPEC_LOOP_PLAN_MAX plan line count that triggers ONE consolidation round
#                      before building (default: 500)
#   SPEC_LOOP_OUTPUT_FORMAT  text (default) | stream-json (live tool events)

set -uo pipefail

trap 'echo -e "\nStopping loop..."; exit 0' INT TERM

# Operate from the repo root so the agent sees the whole tree.
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "Error: not inside a git repository. Run 'git init' in Jumar first." >&2; exit 1; }
cd "$ROOT" || exit 1

LOOP_DIR="tools/spec-loop"
# Jumar layout: specs, plan, and AGENTS live at the repo root, alongside
# the tooling on the same branch — so there is no control-branch/base split.
SPECS_DIR="specs"
PLAN="IMPLEMENTATION_PLAN.md"
AGENTS_FILE="AGENTS.md"
# shellcheck source=tools/spec-loop/lib.sh
. "$LOOP_DIR/lib.sh"

current_branch() {
    local branch
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || return 1
    [ "$branch" = "HEAD" ] && return 0
    printf '%s\n' "$branch"
}

BASE="${SPEC_LOOP_BASE:-main}"
AGENT="${SPEC_LOOP_AGENT:-claude}"
case "${SPEC_LOOP_HARNESS:-$(basename "$AGENT")}" in
    *codex*)    HARNESS=codex ;;
    *cursor*)   HARNESS=cursor ;;
    *gemini*)   HARNESS=gemini ;;
    *opencode*) HARNESS=opencode ;;
    *kiro*)     HARNESS=kiro ;;
    *)          HARNESS=claude ;;
esac
if [ -n "${SPEC_LOOP_MODEL:-}" ]; then
    MODEL="$SPEC_LOOP_MODEL"
elif [ "$HARNESS" = "claude" ]; then
    MODEL="sonnet"
else
    MODEL=""
fi
PR_LIMIT="${SPEC_LOOP_PR_LIMIT:-100}"
OUTPUT_FORMAT="${SPEC_LOOP_OUTPUT_FORMAT:-text}"
PLAN_CONSOLIDATE_THRESHOLD="${SPEC_LOOP_PLAN_MAX:-500}"

# ---- parse arguments -------------------------------------------------
usage() {
    cat <<'EOF'
Spec-driven build loop for Jumar.

Usage:
  ./loop.sh [N]               build; N iterations (omit or 0 = unlimited)
  ./loop.sh build [N]         same as above, explicit
  ./loop.sh plan [N]          gap-analysis only, rewrites the plan (default 1; 0 = unlimited)
  ./loop.sh update [N]        back-fill specs from contributed code (default 1; 0 = unlimited)
  ./loop.sh consolidate [N]   shrink the plan (default 1; 0 = unlimited)
  ./loop.sh -h | --help       show this help

Stop gracefully: Ctrl+C, or `touch STOP` (exits after the current iteration).
EOF
}

case "${1:-}" in
    -h|--help|help)
        usage; exit 0 ;;
    plan)        MODE="plan";        PROMPT_FILE="$LOOP_DIR/PROMPT_plan.md";        MAX_ITERATIONS="${2:-1}" ;;
    update)      MODE="update";      PROMPT_FILE="$LOOP_DIR/PROMPT_update.md";      MAX_ITERATIONS="${2:-1}" ;;
    consolidate) MODE="consolidate"; PROMPT_FILE="$LOOP_DIR/PROMPT_consolidate.md"; MAX_ITERATIONS="${2:-1}" ;;
    build)       MODE="build";       PROMPT_FILE="$LOOP_DIR/PROMPT_build.md";       MAX_ITERATIONS="${2:-0}" ;;
    "")          MODE="build";       PROMPT_FILE="$LOOP_DIR/PROMPT_build.md";       MAX_ITERATIONS=0 ;;
    *)
        if [[ "$1" =~ ^[0-9]+$ ]]; then
            MODE="build"; PROMPT_FILE="$LOOP_DIR/PROMPT_build.md"; MAX_ITERATIONS="$1"
        else
            echo "Error: unknown argument '$1'." >&2
            echo >&2
            usage >&2
            exit 1
        fi ;;
esac

# Reject a non-numeric iteration count so it can never be silently treated as 0
# (i.e. unbounded) by the integer comparisons below.
if ! [[ "$MAX_ITERATIONS" =~ ^[0-9]+$ ]]; then
    echo "Error: iteration count must be a non-negative integer, got '${MAX_ITERATIONS}'." >&2
    exit 1
fi

[ -f "$PROMPT_FILE" ] || { echo "Error: $PROMPT_FILE not found" >&2; exit 1; }

if ! command -v "$AGENT" >/dev/null 2>&1; then
    echo "Error: agent CLI '$AGENT' not found on PATH." >&2
    echo "Set SPEC_LOOP_AGENT to a headless agent CLI (claude / codex / cursor / gemini / opencode / kiro)" >&2
    echo "and SPEC_LOOP_HARNESS to its run convention if the name doesn't imply it." >&2
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Mode:   $MODE"
echo "Prompt: $PROMPT_FILE"
echo "Base:   $BASE  (work items fork from here)"
echo "Agent:  $AGENT"
if [ -n "$MODEL" ]; then echo "Model:  $MODEL"; else echo "Model:  (agent default)"; fi
if [ "$MAX_ITERATIONS" -gt 0 ]; then echo "Max:    $MAX_ITERATIONS iterations"; else echo "Max:    unlimited"; fi
echo "Stop:   Ctrl+C  or  touch STOP"
echo "Note:   this loop never pushes and never opens a PR."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

rm -f STOP
ITERATION=0
CONSOLIDATE_TRIED=false   # one-shot latch; resets when the plan drops back under the limit

spinner() {
    local pid=$1 i=0
    # Index braille frames as array elements (byte-safe under a C/POSIX locale).
    local frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  %s  working... (Ctrl+C to stop)" "${frames[i]}"
        i=$(( (i+1) % ${#frames[@]} )); sleep 0.15
    done
    printf "\r                                              \r"
}

# A cheap, dependency-free repository snapshot, appended to every prompt as a
# routing aid (the Jumar replacement for airflow-steward's uv spec-inventory).
repo_snapshot_context() {
    echo ""
    echo "## Repository snapshot"
    echo ""
    echo "The runner collected this immediately before the iteration, as a"
    echo "routing aid. It is not proof — confirm with a direct file read before"
    echo "changing anything."
    echo ""
    echo "Specs (\`$SPECS_DIR/\`):"
    echo '```'
    git ls-files "$SPECS_DIR" 2>/dev/null || ls "$SPECS_DIR" 2>/dev/null
    echo '```'
    echo ""
    echo "Source & tests:"
    echo '```'
    git ls-files src tests 2>/dev/null | head -200
    echo '```'
    echo ""
    echo "Validation gate (\`make check\`) targets:"
    echo '```'
    grep -E '^[a-z-]+:.*##' Makefile 2>/dev/null
    echo '```'
    echo ""
    echo "Uncommitted changes (\`git status --short\`):"
    echo '```'
    git status --short 2>/dev/null
    echo '```'
}

open_pr_context() {
    echo ""
    echo "## Open pull-request context"
    echo ""
    echo "Treat open pull requests as in-flight work: do not add plan items that"
    echo "are already substantially covered by an open PR, and do not pick a build"
    echo "item that duplicates one."
    echo ""

    if ! command -v gh >/dev/null 2>&1; then
        echo "- unavailable: gh CLI not found on PATH (duplicate-PR check skipped)."
        return 0
    fi

    local prs
    prs="$(gh pr list \
        --state open \
        --limit "$PR_LIMIT" \
        --json number,title,headRefName,baseRefName,url,isDraft \
        --template '{{range .}}- #{{.number}} {{.title}} ({{.headRefName}} -> {{.baseRefName}}){{if .isDraft}} [draft]{{end}} {{.url}}{{"\n"}}{{end}}' \
        2>/dev/null)"
    if [ $? -ne 0 ]; then
        echo "- unavailable: gh pr list failed. Check GitHub auth or network (or there is no remote yet)."
    elif [ -z "$prs" ]; then
        echo "- No open pull requests found."
    else
        printf '%s\n' "$prs"
    fi
}

# Local work-item branches already built. The loop never pushes, so a freshly
# built item has NO open PR and is invisible to the PR check above; listing it
# here is what stops the agent rebuilding it every iteration.
local_branch_context() {
    echo ""
    echo "## Local work-item branches"
    echo ""
    echo "This loop never pushes and never opens a PR, so a work item it already"
    echo "built exists ONLY as a local branch and will NOT appear in the open-PR"
    echo "context above. Treat every branch listed here as already built or in"
    echo "flight: do not add a plan item, and do not pick a build item, whose slug"
    echo "matches one of these or whose change it already carries."
    echo ""

    local have_base=false
    if git rev-parse --verify --quiet "refs/heads/$BASE" >/dev/null 2>&1; then
        have_base=true
    fi

    local branches
    branches="$(git for-each-ref --format='%(refname:short)' refs/heads/ \
        | grep -vxF "$BASE")"

    if [ -z "$branches" ]; then
        echo "- No local work-item branches found (only the base '$BASE')."
        return 0
    fi

    local b subject ahead
    while IFS= read -r b; do
        [ -n "$b" ] || continue
        subject="$(git log -1 --format='%s' "$b" 2>/dev/null)"
        if [ "$have_base" = true ]; then
            ahead="$(git rev-list --count "$BASE..$b" 2>/dev/null)"
            echo "- ${b} (${ahead:-?} commit(s) ahead of ${BASE}): ${subject}"
        else
            echo "- ${b}: ${subject}"
        fi
    done <<< "$branches"
}

while true; do
    if [ "$MAX_ITERATIONS" -gt 0 ] && [ "$ITERATION" -ge "$MAX_ITERATIONS" ]; then
        echo "Reached max iterations: $MAX_ITERATIONS"; break
    fi
    if [ -f STOP ]; then echo "STOP file detected — exiting."; rm -f STOP; break; fi

    ITERATION=$((ITERATION + 1))
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━ LOOP $ITERATION  [ $(date '+%H:%M:%S') ] ━━━━━━━━━━━━━━━━━━━━"

    ACTIVE_PROMPT="$PROMPT_FILE"

    # Build mode: consolidate at most ONCE when the plan grows too long, then
    # build even if still over (the remainder is planned work the consolidate
    # beat preserves by design). The latch resets when the plan drops back under.
    if [ "$MODE" = "build" ]; then
        PLAN_LINES=$( [ -f "$PLAN" ] && wc -l < "$PLAN" || echo 0 )
        if [ "$PLAN_LINES" -le "$PLAN_CONSOLIDATE_THRESHOLD" ]; then
            CONSOLIDATE_TRIED=false
        elif [ "$CONSOLIDATE_TRIED" = false ]; then
            echo "  [plan] $PLAN is ${PLAN_LINES} lines (> ${PLAN_CONSOLIDATE_THRESHOLD}) — one consolidation round"
            ACTIVE_PROMPT="$LOOP_DIR/PROMPT_consolidate.md"
            CONSOLIDATE_TRIED=true
        else
            echo "  [plan] $PLAN still ${PLAN_LINES} lines after consolidation — length is planned work; building"
        fi
    fi

    # A genuine build/update iteration (not a consolidate swap-in) carves a
    # work-item branch off BASE. plan/consolidate stay on the current branch.
    BUILD_ITERATION=false
    if { [ "$MODE" = "build" ] || [ "$MODE" = "update" ]; } && [ "$ACTIVE_PROMPT" = "$PROMPT_FILE" ]; then
        BUILD_ITERATION=true
    fi

    PROMPT_WITH_CONTEXT="$(mktemp "${TMPDIR:-/tmp}/spec-loop-prompt.XXXXXX")" || exit 1
    SNAPSHOT_CONTEXT="$(mktemp "${TMPDIR:-/tmp}/spec-loop-snap.XXXXXX")" || exit 1
    PR_CONTEXT="$(mktemp "${TMPDIR:-/tmp}/spec-loop-prs.XXXXXX")" || exit 1
    BRANCH_CONTEXT="$(mktemp "${TMPDIR:-/tmp}/spec-loop-branches.XXXXXX")" || exit 1
    repo_snapshot_context > "$SNAPSHOT_CONTEXT"
    if [ "$MODE" != "update" ]; then
        open_pr_context > "$PR_CONTEXT"
    fi
    local_branch_context > "$BRANCH_CONTEXT"

    if [ "$BUILD_ITERATION" = true ]; then
        # Fork work items off BASE. Return to it first so every work-item
        # branch starts from the same clean integration point.
        if [ "$(current_branch)" != "$BASE" ]; then
            if ! checkout_out="$(git checkout "$BASE" 2>&1)"; then
                echo "Error: could not check out base '$BASE'. git reported:" >&2
                printf '  %s\n' "$checkout_out" >&2
                echo "Resolve the working tree (commit or stash changes), then re-run." >&2
                rm -f "$PROMPT_WITH_CONTEXT" "$SNAPSHOT_CONTEXT" "$PR_CONTEXT" "$BRANCH_CONTEXT"; break
            fi
        fi
        BASE_HEAD="$(git rev-parse HEAD)"
    fi

    spec_loop_assemble_prompt_file \
        "$ACTIVE_PROMPT" "$PROMPT_WITH_CONTEXT" "$MODE" \
        "$SNAPSHOT_CONTEXT" "$PR_CONTEXT" "$BRANCH_CONTEXT"

    # Run one iteration with a fresh agent context. Harness-specific launch
    # details live in lib.sh so the fixture tests can validate argv without
    # starting an agent.
    spec_loop_launch_agent "$HARNESS" "$AGENT" "$ROOT" "$PROMPT_WITH_CONTEXT" "$MODEL" "$OUTPUT_FORMAT"
    AGENT_PID=$SPEC_LOOP_AGENT_PID
    spinner "$AGENT_PID" & SPINNER_PID=$!
    wait "$AGENT_PID"
    wait "$SPINNER_PID" 2>/dev/null
    rm -f "$PROMPT_WITH_CONTEXT" "$SNAPSHOT_CONTEXT" "$PR_CONTEXT" "$BRANCH_CONTEXT"

    CUR_BRANCH="$(current_branch)"
    LAST_COMMIT="$(git log --oneline -1 2>/dev/null)"
    echo "[ branch ] $CUR_BRANCH"
    [ -n "$LAST_COMMIT" ] && echo "[ commit ] $LAST_COMMIT"

    if [ "$BUILD_ITERATION" = true ]; then
        if [ "$CUR_BRANCH" != "$BASE" ]; then
            echo "[ new branch ] $CUR_BRANCH  (forked off $BASE)"
            echo "               push it with:  git push -u origin $CUR_BRANCH"
        else
            echo "⚠ No work-item branch was created (still on '$CUR_BRANCH'). Check the agent output above." >&2
        fi

        # Safety guard: a build/update iteration must never commit to the base.
        if [ "$CUR_BRANCH" = "$BASE" ] && [ "$(git rev-parse HEAD)" != "$BASE_HEAD" ]; then
            echo "✗ This iteration committed to '$BASE' instead of a work-item branch." >&2
            echo "  Stopping so you can review (expected a new <slug> branch)." >&2
            break
        fi

        # Return to BASE so the tooling (plan, prompts, specs) is present again
        # and you are never stranded on a work branch. The work-item branch persists.
        if [ "$(current_branch)" != "$BASE" ]; then
            git checkout "$BASE" >/dev/null 2>&1 || \
                echo "⚠ Could not return to base '$BASE' (now on '$(current_branch)')." >&2
        fi
    fi
    echo ""
done
