<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Implementation Plan — GetStuffDone

Prioritised **work items** the `build` beat implements one at a time. One work
item = one branch = one PR.

REMINDER (AGENTS.md): build iterations never modify files under `specs/`, and
never weaken a check to get green.

## Status — 2026-08-07

Bootstrap complete. The spec pack (`specs/01`–`05`), `AGENTS.md`, this plan, and
the build loop (`tools/spec-loop/`) exist. `src/getstuffdone/` is a skeleton with
a version, a CLI stub, and a smoke test so `make check` is green from iteration
one. Nothing in the pipeline is implemented yet.

Run `./tools/spec-loop/loop.sh plan` to re-derive this list from the specs, then
`./tools/spec-loop/loop.sh build 1` one item at a time.

## Work items (priority order)

Phase ordering comes from `specs/04-technical-plan.md`. AC ids refer to
`specs/02-functional-spec.md`.

### Phase 0 — skeleton

1. **models-and-invariants** — implement every shape in `specs/03-data-model.md`
   in `src/getstuffdone/models.py` as frozen dataclasses, with the `Check`
   invariants enforced in construction (command⇒argv, file⇒path, judge⇒
   rationale, placeholder-statement rejection). Closes the model half of AC3.1,
   AC3.4.
   *Validation:* `make check` + `python3 -m pytest tests/test_models.py -q`
   (must include a rejection test per invariant).

2. **config-and-capabilities** — `config.py`: load `gsd.toml` / `[tool.gsd]`,
   merge CLI flags, resolve the capability set and the argv allow/deny policy
   (deny wins). Expose one `is_allowed(argv) -> bool` used by every dispatcher.
   *Validation:* `make check` + `pytest tests/test_config.py -q`.

3. **journal-append-and-replay** — `journal.py`: append-only `journal.jsonl`
   with strictly increasing `seq`, plus replay/resume. Closes AC-S1, AC-S2,
   AC-S3.
   *Validation:* `pytest tests/test_journal.py -q`, including a corrupt-trailing
   -line test and a resume test.

### Phase 1 — read-only path

4. **ingest-markdown-todos** — `ingest.py`: parse GFM task lists, nested
   authored subtasks, `@key=value` metadata, stable `item_id`, retained context,
   tolerant parse warnings. Closes AC1.1–AC1.7.
   *Validation:* `pytest tests/test_ingest.py -q` with golden fixture todo files.

5. **select-next-item** — `select.py`: priority then file order, `@depends`
   blocking, cycle detection, journal-aware skip of finished items. Closes
   AC2.1–AC2.5.
   *Validation:* `pytest tests/test_select.py -q`.

6. **cli-plan-dry-run** — `cli.py`: `gsd plan --dry-run` prints parsed items and
   exits 0 with no agent call. First end-to-end read-only path.
   *Validation:* `pytest tests/test_cli.py -q`.

### Phase 2 — decompose + gate

7. **harness-argv** — `harness.py`: the agent-CLI abstraction for claude / codex
   / cursor / gemini / opencode / kiro, with pure, fixture-tested argv
   construction, the scrubbed environment, and the hard deny of `git push` /
   `gh`. Closes AC8.4 at the harness level.
   *Validation:* `pytest tests/test_harness_argv.py -q` with a fake agent that
   records argv; a test that fails if `git push` or `gh` can reach the argv.

8. **decompose-with-required-checks** — `decompose.py`: structured plan request,
   defensive parse, one retry, and the hard rejections (missing check,
   placeholder statement, plan too long, judge without rationale, cyclic
   `depends_on`). Closes AC3.1–AC3.6.
   *Validation:* `pytest tests/test_decompose.py -q` against the fake harness,
   negative case per rejection.

9. **gate-modes** — `gate.py`: `--dry-run` / `--approve` / `--auto`, plus the
   pre-execution capability refusal. Closes AC4.1–AC4.3.
   *Validation:* `pytest tests/test_gate.py -q`.

### Phase 3 — the inner loop (execute and verify land together)

10. **execute-and-verify-command** — `execute.py` **and**
    `verify/command.py` + the verifier registry, in one item. One subtask
    dispatched per call, timeout handling, evidence capture, agent claim stored
    as a claim, verdict from a re-run command. Closes AC5.1–AC5.5, AC6.1,
    AC6.3, AC6.6, AC6.7.
    *Validation:* `pytest tests/test_execute.py tests/test_verify_command.py -q`
    plus a golden-journal test asserting no `attempt_started` precedes the prior
    subtask's `verification`.
    **Do not split this item** — shipping `execute` without a verifier is
    forbidden by AGENTS.md.

11. **verify-file-and-absence** — `verify/file.py`: existence, pattern match,
    hash evidence, and the `absence` kind. Closes AC6.2.
    *Validation:* `pytest tests/test_verify_file.py -q`.

12. **repair-bounded** — `repair.py`: `max_repairs`, failure evidence in the
    repair prompt, rejection of a repair that mutates its own check, correct
    terminal state. Closes AC7.1–AC7.4.
    *Validation:* `pytest tests/test_repair.py -q`, including the
    check-mutation rejection.

### Phase 4 — completion

13. **complete-item** — `complete.py`: checkbox flip byte-preserving, optional
    per-item branch commit, never push. Closes AC8.1–AC8.4.
    *Validation:* `pytest tests/test_complete.py -q` with a byte-diff assertion
    and a temp-git-repo branch assertion.

14. **report-and-resume** — `report.py` + `gsd resume <run-id>`: per-run report,
    exit status contract, partial report from an interrupted journal. Closes
    AC9.1–AC9.3.
    *Validation:* `pytest tests/test_report.py tests/test_resume.py -q`.

### Phase 5 — remaining verifiers

15. **verify-judge-adversarial** — `verify/judge.py`: fresh context, transcript
    exclusion asserted by inspecting the assembled prompt, default-fail framing,
    unparseable ⇒ inconclusive. Closes AC6.4.
    *Validation:* `pytest tests/test_verify_judge.py -q`.

16. **verify-manual** — `verify/manual.py`: interactive confirm;
    `--non-interactive` ⇒ inconclusive, never auto-pass. Closes AC6.5.
    *Validation:* `pytest tests/test_verify_manual.py -q`.

### Phase 6 — polish

17. **gsd-doctor** — config, harness availability, allow-list sanity, todo-file
    parse check in one command.
    *Validation:* `pytest tests/test_doctor.py -q`.

18. **ci-and-coverage-floor** — GitHub Actions running `make check`, coverage
    floor at 90% per module once Phase 1 has landed.
    *Validation:* the workflow green on a scratch branch.

## Guardrails (do not re-plan these)

- **Verification is not optional and not deferrable.** No work item may ship a
  stage that performs work ahead of the check that proves it worked. Item 10 is
  deliberately two modules in one branch for this reason.
- **A check may never be weakened to pass.** Not a test, not an AC, not a
  subtask's own `Check` at runtime (that is what AC7.3 exists to enforce).
- **`network` is not a default capability**, and `curl`/`wget`/`ssh`/`scp` stay
  on the standing deny list. Do not plan a work item that relaxes this for
  convenience.
- **No `shell=True`.** Every subprocess is argv. Do not plan a shell-string
  escape hatch.
- **Out of scope for v1** (do not plan): parallel subtask execution, cross-item
  planning, non-Markdown todo inputs, sync with external trackers (Jira, Asana,
  Todoist), a resident daemon, a web UI.
- **The product pointing at its own plan** is a milestone, not a dependency.
  Nothing in `src/getstuffdone/` may assume it is being run against this repo.

## Manual follow-ups (USER-side; not loop work items — do not build these)

- Decide whether `todo.md` and `runs/` should be git-ignored in your working
  copy (the shipped `.gitignore` ignores `runs/`, `gsd.toml` and `todo.md` by
  default — remove those lines if you want the list tracked).
- Confirm which agent CLI is on PATH before the first loop run
  (`SPEC_LOOP_AGENT`, default `claude`).
- Run the loop inside a sandbox with no push credentials in the environment.
