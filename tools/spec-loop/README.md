<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# spec-loop (GetStuffDone)

A spec-driven build loop in the [Ralph](https://ghuntley.com/ralph/) style — run
a fresh agent context against a fixed prompt, repeat — that reconciles the
GetStuffDone **code** against the GetStuffDone **specs**.

This is the meta-layer: GetStuffDone is a system that decomposes work, does one
step at a time, and verifies each step before advancing — and it is *built* the
same way. See `../../specs/05-operator-tooling.md` for the mapping between the
two.

The loop drives a **headless agent CLI**. `SPEC_LOOP_AGENT` picks it (default
`claude`) and the harness convention defaults from the CLI name, so
`SPEC_LOOP_AGENT=codex ./tools/spec-loop/loop.sh build 5` is usually all you
need. Supported: `claude`, `codex`, `cursor`/`cursor-agent`, `gemini`,
`opencode`, `kiro`.

## Prerequisites

- **Runtime:** Bash + coreutils; Python 3.11+ for the tool the loop builds.
- **Git:** the loop must run inside a git checkout (`git init` first).
- **Agent CLI:** one supported headless agent (default `claude`).
- **`gh`** (optional): only used to list open PRs for the duplicate-work check;
  the loop degrades gracefully without it.

## The pieces

| File | Role |
|---|---|
| `../../specs/` | The functional description of the product — the desired state the loop reconciles code against. |
| `../../IMPLEMENTATION_PLAN.md` | Prioritised **work items** (the gaps). One work item = one branch = one PR. |
| `../../AGENTS.md` | Loop-scoped operational rules (repo map, validation commands, branch + hard-limit rules). |
| `PROMPT_plan.md` / `PROMPT_build.md` / `PROMPT_update.md` / `PROMPT_consolidate.md` | The per-beat prompts. |
| `loop.sh` | The runner. |
| `lib.sh` | Deterministic prompt-assembly + agent-launch helpers (unit-tested without launching an agent). |
| `tests/test_runner_fixtures.sh` | Fixture tests for `lib.sh`. |

## First-time setup

```bash
cd /path/to/GetStuffDone
git init && git add -A && git commit -m "Initial spec pack + loop"
git branch -M main
```

## Modes

```bash
./tools/spec-loop/loop.sh              # build, unlimited iterations
./tools/spec-loop/loop.sh 10           # build, max 10 iterations
./tools/spec-loop/loop.sh plan         # gap-analysis -> rewrite the plan (no code changes)
./tools/spec-loop/loop.sh update       # back-fill specs from code contributed the normal way
./tools/spec-loop/loop.sh consolidate  # shrink the plan when it grows too long
```

- **plan** — compares `specs/` against the code and rewrites
  `IMPLEMENTATION_PLAN.md`. Plans only; no commits.
- **build** — implements the single highest-priority work item on its own
  `<slug>` branch, validates with `make check`, and commits there. Skips items
  already covered by an open PR or an existing local branch.
- **update** — the inverse of plan: finds code with no spec and brings the specs
  back in sync, on a `sync-specs-<timestamp>` branch.
- **consolidate** — shrinks the plan without losing planned work (build
  auto-switches to this once for a plan over `SPEC_LOOP_PLAN_MAX` lines).

Recommended first run: `./tools/spec-loop/loop.sh plan` to turn the specs into a
concrete plan, review it, then `./tools/spec-loop/loop.sh build 1` one item at a
time until you trust it.

## The three non-negotiables

- **A branch per work item.** build/update never commit to the integration base.
- **Never pushes, never opens a PR.** Each beat ends at a local commit and
  prints the exact `git push` + `gh pr create --web` commands.
- **Green before commit.** A build iteration that cannot get `make check` green
  does not commit — and never gets there by weakening a test.

## Stop / configure

- Stop: `Ctrl+C`, or `touch STOP` (exits after the current iteration).
- `SPEC_LOOP_BASE` — branch to fork work items from (default `main`).
- `SPEC_LOOP_AGENT` — headless agent CLI (default `claude`).
- `SPEC_LOOP_HARNESS` — override the invocation convention when the CLI name
  doesn't imply it.
- `SPEC_LOOP_MODEL` — model passed to the agent CLI (default `sonnet` for Claude).
- `SPEC_LOOP_PR_LIMIT` — open PRs to include in duplicate-work checks (default `100`).
- `SPEC_LOOP_PLAN_MAX` — plan line count that triggers one consolidation round
  before building (default `500`).
- `SPEC_LOOP_OUTPUT_FORMAT` — `text` (default) or `stream-json` to watch live
  tool-call events when debugging.

## Security

The loop runs the agent with its unattended / auto-approval flag (for Claude,
`--dangerously-skip-permissions`), which bypasses the **agent** permission layer
but not an external OS sandbox. Launch it inside a sandbox with **no** push/write
credentials in the environment. For Claude the loop also hard-denies `git push`
and `gh` via `--disallowedTools` as defence in depth. Test the runner's argv
without launching an agent:

```bash
bash tools/spec-loop/tests/test_runner_fixtures.sh
```
