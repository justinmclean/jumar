<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# AGENTS — GetStuffDone operational context

This file is the operational context the spec-loop's prompts load in addition to
the beat prompt itself: the repository map, the validation commands, and the
branch rules. GetStuffDone is a single-developer Python project — no skills, no
plugin, no separate governance layer.

## What this project is

A system that takes a plain Markdown todo list and, for each item, decomposes it
into subtasks, executes them **one at a time**, and **verifies each one against
an executable check before the next one starts**. The verified subtask — not the
agent's claim of success — is the unit of trust. See `specs/01-product-spec.md`.

The repo is built by the same discipline it implements (`specs/05-operator-tooling.md`).

## Repository map (what the loop edits)

- `specs/` — the functional description of the product (the desired state the
  build loop reconciles code against). One file per area. Owned by the human and
  by the `update` beat; **build iterations never touch it**.
- `src/getstuffdone/` — the Python package: the pipeline stages (ingest, select,
  decompose, gate, execute, verify, repair, complete, report), the journal, the
  agent harness, and the CLI.
- `tests/` — pytest suites, one module per stage plus fixtures and golden
  journals.
- `IMPLEMENTATION_PLAN.md` — prioritised work items (the gaps). One work item =
  one branch = one PR. Owned by the `plan`/`consolidate` beats.
- `tools/spec-loop/` — the build loop itself (bash). See its README.
- `AGENTS.md` — this file.
- `todo.md`, `gsd.toml`, `runs/` — the human's own work list, config, and run
  journals. Git-ignored where personal; never commit a real todo list with
  private content.

## Validation commands (the build "backpressure" step)

Run the chosen work item's own **Validation** block first. General checks:

```bash
make check                     # ruff + mypy + pytest — what CI runs

python3 -m pytest -q           # tests only
python3 -m pytest tests/test_verify_command.py -q    # one stage
ruff check src tests tools
mypy src/getstuffdone

# Shell tooling (when a work item touches the loop itself)
bash -n tools/spec-loop/loop.sh tools/spec-loop/lib.sh
bash tools/spec-loop/tests/test_runner_fixtures.sh
```

A work item that adds or changes a pipeline stage must add or extend that
stage's pytest module. A stage without a test is incomplete. Both the new tests
and the existing suite must pass before committing.

**Negative tests are not optional.** Every stage's acceptance criteria in
`specs/02-functional-spec.md` include refusal paths — unverifiable plans,
self-modified checks, ungranted capabilities, missing verifier binaries, corrupt
journals. Those paths *are* the product; a work item that only tests the happy
path is incomplete.

## Branch rules (one branch per fix/feature)

- **Never commit feature work to the integration base** (`$SPEC_LOOP_BASE`,
  default `main`). `build` branches a bare `<slug>` off it first.
- **One work item per branch, one branch per PR.** Do not bundle work items.
- A `build` branch does not edit `specs/` and does not edit
  `IMPLEMENTATION_PLAN.md` (except to record a blocker when it stops without
  committing) — the plan is reconciled by a later `plan` pass, which avoids
  cross-branch conflicts.
- The `update` beat branches `sync-specs-<timestamp>` and edits `specs/` **only**
  — it documents reality, it never changes source or tests.
- The runner feeds each iteration **both** the open PRs and the local work-item
  branches as in-flight work, because a built-but-unpushed item exists only as a
  local branch — that list is what stops the loop rebuilding the same item every
  iteration.

## Hard limits (do not cross)

- **No push, no PR.** The loop stops at a local commit and prints the human-run
  `git push` + `gh pr create --web` commands. Opening the PR is the human's step.
  The *product* has the same rule: it may commit on a per-item branch, never push.
- **Never weaken a check to make it pass.** Do not delete, skip, `xfail`, or
  loosen a test or an acceptance criterion to get green. If a criterion is
  wrong, note it in `IMPLEMENTATION_PLAN.md` and stop — the human decides. This
  is the one rule whose violation invalidates the whole project.
- **Never ship execution without its verification.** No work item may land a
  stage that performs work ahead of the check that proves it worked
  (`specs/04-technical-plan.md` §Phases).
- **No shell strings.** Every subprocess in the product is argv with
  `shell=False`. No `shell=True`, ever.
- **No secrets in the repo.** API keys come from environment variables or a
  git-ignored `.env`, never committed. Never commit a real `todo.md` or `runs/`.
- **Least authority by default.** `network` is not a default capability, and
  `git push` / `gh` are hard-denied in every argv the product dispatches.

## Commits

- Imperative subject describing the user-visible change.
- Trailer `Generated-by: <agent> (<model>)`, where `<agent>` and `<model>` are
  the actual agent and model running (e.g. `Claude (Sonnet 4.5)`). Do not
  hardcode either, and never add a `Co-Authored-By:` trailer for an agent.
- One commit per build iteration.

## Spec files are read-only for build iterations

Build iterations MUST NOT modify any file under `specs/`. No `status:`
frontmatter, no "known gaps" comments, no progress notes — the specs are the
source of truth, not a scratchpad, and header stamps conflict across branches.
Progress and gap notes belong in `IMPLEMENTATION_PLAN.md`. Only `plan`/`update`
beats (or the human) may touch `specs/`.
