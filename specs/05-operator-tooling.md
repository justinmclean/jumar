<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->
---
status: done
---

# 05 — Operator Tooling: the spec loop that builds this repo

This spec is **about the meta-layer, not the product**. Jumar is built by
the same kind of loop it implements: a spec-driven build loop that reconciles the
code under `src/jumar/` against the specs in `specs/`.

Status is `done` because the loop described here exists at
`tools/spec-loop/` — it is the bootstrap, and it shipped first, by design.

## Why the meta-layer exists

The product's central claim is that work should be decomposed, executed one step
at a time, and verified before advancing. A system that made that claim while
being built by ad-hoc prompting would not be credible. So the repo is built the
way the product works:

| product concept | build-loop equivalent |
|---|---|
| todo item | work item in `IMPLEMENTATION_PLAN.md` |
| decomposition | the `plan` beat (specs → prioritised work items) |
| one subtask at a time | one work item per `build` iteration, on its own branch |
| acceptance check | the work item's **Validation** command (`make check`) |
| verification gate | the build beat must not commit until validation passes |
| journal | git history: one commit per verified work item |
| repair | the build beat fixing until validation passes, then stopping |

The loop is the product's own dogfood, one level up.

## The four beats

Implemented in `tools/spec-loop/loop.sh` (runner) and `lib.sh` (deterministic
prompt assembly + agent launch, unit-testable without an agent):

- **plan** — read `specs/`, compare against the code, rewrite
  `IMPLEMENTATION_PLAN.md` as prioritised work items. Plans only; no code, no
  commits.
- **build** — implement the single highest-priority work item on its own bare
  `<slug>` branch, run its Validation command, fix until green, commit locally.
  One work item = one branch = one PR.
- **update** — the inverse of plan: find functionality that landed the normal
  way and back-fill `specs/` on a `sync-specs-<timestamp>` branch. Edits specs
  only.
- **consolidate** — shrink `IMPLEMENTATION_PLAN.md` when it grows past
  `SPEC_LOOP_PLAN_MAX` lines, without dropping a single planned work item.

## Non-negotiables

- **A branch per work item.** `build`/`update` never commit to the integration
  base; each carves its own branch off it.
- **Never pushes, never opens a PR.** Every beat ends at a local commit and
  prints the human-run `git push` + `gh pr create --web` commands.
- **Validation is the gate.** A build iteration that cannot get `make check`
  green does not commit; it records the blocker in the plan and stops.
- **No redoing built work.** Because the loop never pushes, a built item exists
  only as a local branch. Every iteration is fed both open PRs and local
  work-item branches as in-flight work.
- **Specs are read-only to build iterations.** Progress notes go in
  `IMPLEMENTATION_PLAN.md`; only `plan`/`update`/the human touch `specs/`.

## Acceptance criteria (for the loop itself)

- AC-L1 `bash -n tools/spec-loop/loop.sh tools/spec-loop/lib.sh` parses clean.
- AC-L2 `bash tools/spec-loop/tests/test_runner_fixtures.sh` passes: prompt
  assembly includes snapshot/PR/branch context in the right modes, and each
  harness's argv carries its unattended flag.
- AC-L3 The Claude argv contains `--disallowedTools "Bash(git push:*)"` and
  `"Bash(gh:*)"`.
- AC-L4 An unknown mode argument exits non-zero with usage.
- AC-L5 A non-numeric iteration count is rejected rather than silently treated
  as unlimited.
- AC-L6 A build iteration that ends on the base branch with a changed HEAD stops
  the loop with an error.

## Relationship to the product

`tools/spec-loop/` builds the repo. `src/jumar/` is what gets built. They
share ideas — the harness abstraction, the never-push rule, the fixture-tested
argv — but not code: the loop is bash the human runs, the product is Python the
human installs. Once the product is complete enough, Jumar can be pointed
at its own `IMPLEMENTATION_PLAN.md`; that is a milestone, not a dependency, and
nothing in the product may assume it.

## Scheduling the loop

The loop is a plain command, so it schedules the same way the product does — via
cron/launchd/systemd, not a daemon. An overnight `./tools/spec-loop/loop.sh
build 3` is a reasonable cadence once you trust it. Two cautions specific to the
loop:

- **Single-flight matters more here**, because two concurrent loops would fork
  work-item branches off the same base and duplicate work. The loop has no lock
  of its own (the product's `lock.py` guards the product, not the loop) — so a
  scheduled loop must not overlap its own run window.
- **Nothing is pushed**, so a scheduled loop accumulates local work-item
  branches for review. That is the intended shape: the machine builds, the human
  merges.

## Known gaps

- The loop does not measure whether a build iteration's Validation command was
  actually run (it trusts the beat prompt). A future `SPEC_LOOP_VERIFY=1` mode
  could re-run `make check` in the runner after each iteration — the runner-side
  equivalent of the product's stage 6.
- The loop has no single-flight lock, so an overlapping scheduled invocation
  would duplicate work. Until it does, keep the cadence longer than the longest
  plausible run.
