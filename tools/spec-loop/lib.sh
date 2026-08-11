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
