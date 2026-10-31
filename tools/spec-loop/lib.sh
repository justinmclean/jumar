#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#   https://www.apache.org/licenses/LICENSE-2.0
#
# Deterministic helpers for tools/spec-loop/loop.sh. Keep this file free of
# live agent execution so the fixture tests can pin runner behaviour without
# launching a headless harness.
#
# Adapted for Jumar from the airflow-steward spec-loop: this project is a
# plain Python tool (src/ + tests/), so there is no control-branch-vs-base
# split, no .claude/skills, and no uv/spec-inventory dependency. Prompt
# assembly is therefore just: base prompt + repo snapshot + PR context +
# local-branch context.

# Assemble the full per-iteration prompt from its parts, in order.
#   $1 prompt_file   the beat prompt (PROMPT_build.md, etc.)
#   $2 dest          file to write the assembled prompt to
#   $3 mode          plan | build | update | consolidate
#   $4 snapshot_file repo snapshot context (specs / src / tests / git status)
#   $5 pr_file       open pull-request context
#   $6 branch_file   local work-item branch context
spec_loop_assemble_prompt_file() {
    local prompt_file=$1 dest=$2 mode=$3 snapshot_file=$4 pr_file=$5 branch_file=$6

    cat "$prompt_file" > "$dest"
    cat "$snapshot_file" >> "$dest"
    # Update mode diffs code against specs; it never picks a work item, so the
    # open-PR list buys nothing there.
    if [ "$mode" != "update" ]; then
        cat "$pr_file" >> "$dest"
    fi
    cat "$branch_file" >> "$dest"
}

# Launch one non-interactive agent iteration, reading the assembled prompt.
# Kept deterministic (no waiting) so fixtures can assert the argv. Sets
# SPEC_LOOP_AGENT_PID for the caller to wait on.
#   $1 harness  claude | codex | cursor | gemini | opencode | kiro
#   $2 agent    the CLI to exec
#   $3 root     repo root
#   $4 prompt_file
#   $5 model    may be empty (agent default)
#   $6 output_format  text | stream-json
spec_loop_launch_agent() {
    local harness=$1 agent=$2 root=$3 prompt_file=$4 model=$5 output_format=$6
    local model_args=()
    [ -n "$model" ] && model_args=(--model "$model")

    if [ "$harness" = "opencode" ]; then
        local oc_format_args=()
        [ "$output_format" = "stream-json" ] && oc_format_args=(--format json)
        "$agent" run \
            --auto \
            ${model_args[@]+"${model_args[@]}"} \
            ${oc_format_args[@]+"${oc_format_args[@]}"} \
            "$(cat "$prompt_file")" &
    elif [ "$harness" = "codex" ]; then
        local codex_format_args=()
        [ "$output_format" = "stream-json" ] && codex_format_args=(--json)
        "$agent" exec \
            --dangerously-bypass-approvals-and-sandbox \
            --cd "$root" \
            ${model_args[@]+"${model_args[@]}"} \
            ${codex_format_args[@]+"${codex_format_args[@]}"} \
            - < "$prompt_file" &
    elif [ "$harness" = "cursor" ]; then
        local cursor_format_args=(--output-format "$output_format")
        local cursor_subcommand=()
        [ "$(basename "$agent")" = "cursor" ] && cursor_subcommand=(agent)
        "$agent" \
            ${cursor_subcommand[@]+"${cursor_subcommand[@]}"} \
            --print \
            --force \
            --trust \
            --workspace "$root" \
            ${model_args[@]+"${model_args[@]}"} \
            ${cursor_format_args[@]+"${cursor_format_args[@]}"} \
            "$(cat "$prompt_file")" &
    elif [ "$harness" = "gemini" ]; then
        "$agent" \
            --yolo \
            ${model_args[@]+"${model_args[@]}"} \
            --prompt "$(cat "$prompt_file")" &
    elif [ "$harness" = "kiro" ]; then
        "$agent" chat --no-interactive \
            "$(cat "$prompt_file")" &
    else
        # Claude Code (default). Bypasses the AGENT permission layer, not the
        # OS sandbox; hard-denies push and gh as defence in depth.
        local verbose_args=()
        [ "$output_format" = "stream-json" ] && verbose_args=(--verbose)
        "$agent" -p \
            --dangerously-skip-permissions \
            --disallowedTools "Bash(git push:*)" "Bash(gh:*)" \
            --output-format="$output_format" \
            ${verbose_args[@]+"${verbose_args[@]}"} \
            ${model_args[@]+"${model_args[@]}"} < "$prompt_file" &
    fi
    SPEC_LOOP_AGENT_PID=$!
}

# ---- single-flight lock ------------------------------------------------
# The product's lock semantics (src/jumar/lock.py, AC10.5 / AC10.6), ported to
# the runner: a PID-stamped lock file, a live lock refuses the second run with
# `already_running`, a stale lock (dead PID or unreadable file) is reclaimed.
# Kept free of exits so the fixture tests can drive it directly; the caller
# decides what a refused acquire means (loop.sh exits 0).

# Return 0 if $1 names a running process.
spec_loop_pid_alive() {
    local pid=$1
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null
}

# Acquire the lock, or report why not.
#   $1 lock_file
#   $2 pid         PID to stamp (default: this shell)
# Returns 0 when the lock is held by $2 on return, printing `reclaimed stale
# lock (pid=N)` to stdout when a dead holder was swept aside. Returns 1 when a
# live holder exists, printing `already_running (pid=N)`; the lock file is
# left untouched in that case.
spec_loop_lock_acquire() {
    local lock_file=$1 pid=${2:-$$}
    local attempt existing
    for attempt in 1 2; do
        # noclobber makes the create atomic: exactly one writer wins.
        if ( set -C; printf 'pid=%s\n' "$pid" > "$lock_file" ) 2>/dev/null; then
            return 0
        fi
        existing="$(sed -n 's/^pid=\([0-9][0-9]*\)$/\1/p' "$lock_file" 2>/dev/null | head -n1)"
        if [ -n "$existing" ] && spec_loop_pid_alive "$existing"; then
            printf 'already_running (pid=%s)\n' "$existing"
            return 1
        fi
        # Dead holder, or a file we cannot parse: stale either way.
        printf 'reclaimed stale lock (pid=%s)\n' "${existing:-unknown}"
        rm -f "$lock_file"
    done
    # Lost the race twice to a holder that then vanished; refuse rather than loop.
    printf 'already_running (pid=unknown)\n'
    return 1
}

# Release the lock, but only if it is still ours: never remove another run's.
#   $1 lock_file
#   $2 pid         PID stamped at acquire (default: this shell)
spec_loop_lock_release() {
    local lock_file=$1 pid=${2:-$$}
    [ -f "$lock_file" ] || return 0
    if [ "$(sed -n 's/^pid=\([0-9][0-9]*\)$/\1/p' "$lock_file" 2>/dev/null | head -n1)" = "$pid" ]; then
        rm -f "$lock_file"
    fi
}
