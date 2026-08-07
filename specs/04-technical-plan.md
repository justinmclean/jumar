<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->
---
status: proposed
---

# 04 — Technical Plan

## Stack

- **Python 3.11+**, standard library first. Third-party dependencies are
  justified per addition, not assumed.
- **pytest** for tests, **ruff** for lint/format, **mypy** for types.
- **Bash + git** for the spec loop that builds this repo (`tools/spec-loop/`).
- No database. Durable state is `runs/<run-id>/journal.jsonl` plus artefact
  files; the todo file itself stays the human-facing source of work.

## Package layout

```
src/getstuffdone/
  __init__.py
  cli.py            # `gsd` entry point: run / plan / resume / report / doctor
  config.py         # gsd.toml + CLI flag resolution, capability + argv policy
  models.py         # every shape in 03-data-model.md, with invariants
  ingest.py         # stage 1
  select.py         # stage 2
  decompose.py      # stage 3
  gate.py           # stage 4
  execute.py        # stage 5
  verify/
    __init__.py     # registry: kind -> verifier
    command.py      # stage 6, kind=command
    file.py         #          kind=file / absence
    judge.py        #          kind=judge  (adversarial, fresh context)
    manual.py       #          kind=manual
  repair.py         # stage 7
  complete.py       # stage 8 (checkbox flip, optional per-item commit)
  report.py         # stage 9
  journal.py        # append-only journal + replay/resume
  harness.py        # agent CLI abstraction (see below)
tests/              # one module per stage, plus fixtures/ and golden/
```

**Single source of truth.** Each stage owns its logic; no stage reimplements
another's. New behaviour extends the owning module rather than adding a parallel
one.

## The harness abstraction

`harness.py` is the only place that knows how to invoke an agent. It exposes:

```python
def run_agent(prompt: str, *, cwd: Path, capabilities: set[Capability],
              timeout_s: int, harness: HarnessInfo) -> AgentResult
```

Deliberately mirroring `tools/spec-loop/lib.sh`, it supports `claude`, `codex`,
`cursor`, `gemini`, `opencode` and `kiro`, selecting flags by harness name. Two
consequences worth naming:

1. **Argv construction is pure and unit-tested** without launching an agent —
   the same fixture-test trick the spec loop uses (`tests/test_harness_argv.py`
   substitutes a fake agent that records its argv).
2. **Capability enforcement happens here**, at the single choke point: the
   allow/deny argv[0] policy and the network/git flags are applied when the
   command line is built, so no stage can accidentally bypass them.

Structured output (plans, judge verdicts) is requested as JSON and parsed
defensively: a response that does not validate against the model is a rejection,
never a partial accept.

## Execution isolation

- Subtasks run with `cwd` set to the item's working directory (default: repo
  root) and inherit a **scrubbed environment**: no `GITHUB_TOKEN`, no
  `*_API_KEY` beyond the one the harness itself needs, no SSH agent socket.
- `network` is not a default capability. Without it the harness is launched with
  its offline/no-tool-network flags where the CLI supports them, and `curl`,
  `wget`, `ssh`, `scp` are on the standing deny list regardless.
- `git push` and `gh` are **hard-denied** in every dispatched argv, mirroring the
  spec loop's `--disallowedTools` defence in depth. AC8.4 tests this.
- Command checks use `subprocess.run` with an argv list, `shell=False`, a
  timeout, and captured output. No shell string interpolation, anywhere.

## Verifier design

The registry maps `CheckKind → Verifier` protocol
(`def verify(check: Check, ctx: VerifyContext) -> VerificationResult`). Adding a
kind is: write the module, register it, add a test (AC6.7 asserts a dummy kind
can be registered end to end without touching `execute.py`).

The `judge` verifier is the delicate one. Its contract:

- It is given the acceptance `statement`, the artefacts (file contents, diffs,
  command outputs) and **nothing from the executing agent's transcript**.
- Its prompt frames the default answer as **fail**: it must cite specific
  evidence to return `pass`, and "looks reasonable" is explicitly not evidence.
- It returns structured `{verdict, reason, artefacts_shown}`; an unparseable
  response is `inconclusive`, not a pass.

## Ordering and concurrency

v1 is strictly sequential: one item, one subtask, one verification at a time.
The journal invariant (`attempt_finished` → `verification` before the next
`attempt_started`) is what makes that testable. Intra-item parallelism across
independent `depends_on` branches is a **later** phase and must not weaken that
invariant — it would be reformulated per dependency chain, not dropped.

## Phases (the order the loop should build in)

- **Phase 0 — skeleton.** `models.py` with invariants, `config.py`,
  `journal.py`, `cli.py` shell, test scaffolding, `make check` green.
- **Phase 1 — read-only path.** `ingest.py`, `select.py`, `gsd plan --dry-run`
  printing the parsed items. No agent calls yet.
- **Phase 2 — decompose + gate.** `harness.py`, `decompose.py`, `gate.py`;
  `gsd plan` produces and journals a validated plan. Still executes nothing.
- **Phase 3 — the inner loop.** `execute.py`, `verify/command.py`,
  `verify/file.py`, `repair.py`. This is the first phase that can do work, and
  the first that can be trusted, because verification lands with execution —
  never before it in the build order.
- **Phase 4 — completion.** `complete.py` (checkbox flip, per-item branch
  commit), `report.py`, `gsd resume`.
- **Phase 5 — the remaining verifiers.** `verify/judge.py`,
  `verify/absence.py`, `verify/manual.py`.
- **Phase 6 — polish.** `gsd doctor` (config + harness + allow-list sanity),
  richer reports, evidence artefact pruning.

**Sequencing rule:** no phase that executes work may land before the
verification it depends on. Phase 3 ships `execute` and `verify` together, in
one work item or two strictly-ordered ones.

## Decisions

- **Markdown todo file, not a database.** The list must stay editable by hand in
  any editor; state that belongs to the tool lives in `runs/`, never in the
  user's file beyond the checkbox flip.
- **Journal before side effect.** Every stage writes its journal entry before
  the action it describes completes, so a crash leaves the record ahead of
  reality rather than behind it.
- **Verification in a fresh context.** Reusing the executor's context would make
  the check a self-assessment, which is precisely what the product exists to
  avoid.
- **No push, no PR, ever.** The system stops at a local commit and prints the
  commands. Same rule as the spec loop, for the same reason.
- **Bounded repairs over unbounded retry.** An agent that cannot pass a check in
  three tries is not converging; more attempts mostly produce more damage.
- **Deferred:** parallel subtasks, cross-item planning, non-Markdown inputs,
  external task-tracker sync, a resident daemon.

## Testing strategy

- One pytest module per stage, named for the stage.
- **Fake harness** fixture: a deterministic agent stub returning canned plans
  and claims, so stages 3–7 are tested without a live model.
- **Golden journals**: fixture runs whose `journal.jsonl` is asserted whole,
  which is how the ordering invariants stay honest.
- **Negative tests are first-class**: unverifiable plans, self-modified checks,
  ungranted capabilities, missing verifier binaries, corrupt journals. The
  product is a refusal machine; most of its value is in the paths where it says
  no.
- Coverage floor enforced in CI once Phase 1 lands; every module ≥ 90%.
