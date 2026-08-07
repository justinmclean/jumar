<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# GetStuffDone

Turn a plain Markdown todo list into **verified** work.

For each item on the list, GetStuffDone breaks it into concrete subtasks, does
**one subtask at a time**, and **proves each one worked** — with a real,
executable check — before starting the next. An item is done when every one of
its subtasks passed its own check. If a check cannot be passed, the item stops
and says exactly which subtask and which check failed.

The unit of trust is the verified subtask, not the agent's claim of success.

```
todo file → ingest → select ─┬─ time eligibility (@not-before / @due / @every)
                             └─ dependency eligibility (@depends)
          → decompose → gate → ┌ execute one subtask ┐
                               │ verify it          │ ← repeat
                               └ repair (bounded)   ┘
                               → complete item → report
```

Runs themselves can recur: `gsd schedule add "0 9 * * 1-5"` installs a weekday
entry in cron/launchd/systemd and exits. There is no resident daemon.

## Why this exists

Handing "migrate the export script" to an agent produces *something*. Nothing
checks that it works, and nothing notices that steps two through five were never
done. The failure mode is confident, unverified, partial completion —
so the whole design is built around refusing to advance on unproven work:

- A subtask with no executable acceptance check is **rejected at planning time**.
  "Trust me" is not a check.
- Verification runs in a **fresh context** that never sees the executing agent's
  reasoning — only the world it left behind.
- The independent judge verifier is prompted **adversarially**: its default
  answer is fail, and it must cite specific evidence to pass.
- A check that cannot run is `inconclusive`, which is **not** a pass.
- Repairs are **bounded**; a repair may never rewrite its own acceptance check.
- The system **never pushes and never opens a PR**. It stops at a local commit.
- A deadline changes only **queue position**. Being overdue never shortens a
  plan, relaxes a check, or skips verification — a test asserts `due` is unread
  by the decompose, execute, verify, and repair paths.
- A scheduled run is **non-interactive by construction**: anything needing a
  human ends `inconclusive` and waits, rather than being auto-passed.

## Status

**Bootstrap.** The specification, the operational rules, the prioritised plan,
and the build loop exist. The pipeline itself is not implemented yet — that is
what the loop builds, one verified work item at a time.

## Layout

| Path | What it is |
|---|---|
| `specs/` | The specification: product & vision, functional spec with numbered acceptance criteria, data model, technical plan, and the spec for the loop itself. |
| `IMPLEMENTATION_PLAN.md` | Prioritised work items. One work item = one branch = one PR. |
| `AGENTS.md` | Operational rules for any agent working in this repo: repo map, validation commands, branch rules, hard limits. |
| `tools/spec-loop/` | The spec-driven build loop that builds this repo. |
| `src/getstuffdone/` | The Python package (skeleton; built by the loop). |
| `tests/` | pytest suites, one module per stage. |
| `todo.example.md` | An example todo list showing the supported syntax. |

## The spec pack

- **[01 — Product & Vision](specs/01-product-spec.md)** — the problem, the
  goals, the non-goals, the guiding principles.
- **[02 — Functional Spec](specs/02-functional-spec.md)** — the ten stages,
  each with behaviour, contract, and numbered acceptance criteria (AC1.1 …
  AC10.9) that tests check directly.
- **[03 — Data Model](specs/03-data-model.md)** — every shape, its invariants,
  and the append-only journal format.
- **[04 — Technical Plan](specs/04-technical-plan.md)** — stack, package layout,
  the agent-harness abstraction, execution isolation, the clock/scheduling
  rules, and the **build phases**.
- **[05 — Operator Tooling](specs/05-operator-tooling.md)** — the spec loop, and
  the mapping between how the product works and how the repo is built.

## Getting started

```bash
git init && git add -A && git commit -m "Initial spec pack + loop"
git branch -M main
make install                       # editable install + dev tools
make check                         # ruff + mypy + pytest + loop fixture tests

./tools/spec-loop/loop.sh plan     # derive work items from the specs
./tools/spec-loop/loop.sh build 1  # build exactly one, on its own branch
```

Once the pipeline is built, a recurring run installs with:

```bash
gsd schedule add "0 9 * * 1-5" --todo ~/todo.md --dry-run   # see it first
gsd schedule add "0 9 * * 1-5" --todo ~/todo.md             # then install
gsd schedule list
```

Review the branch, push it yourself, open the PR yourself. The loop stops at a
local commit by design.

## How the repo is built

GetStuffDone is built the way GetStuffDone works — one work item at a time, on
its own branch, validated before it commits:

| product concept | build-loop equivalent |
|---|---|
| todo item | work item in `IMPLEMENTATION_PLAN.md` |
| decomposition | the `plan` beat |
| one subtask at a time | one work item per `build` iteration |
| acceptance check | the item's Validation command (`make check`) |
| verification gate | no commit until validation is green |
| journal | git history — one commit per verified item |

Full detail in [`tools/spec-loop/README.md`](tools/spec-loop/README.md).

## Todo file syntax

```markdown
Context prose above an item is passed to the agent as background,
never treated as work.

- [ ] Add a --json flag to the export script @priority=1 @capability=write_fs
- [ ] Update the README install section @depends=export-json
      - [ ] Rewrite the install steps for the new flag
      - [ ] Check every command in the README actually runs
- [ ] Rotate the backup logs @every=weekday
- [ ] Draft the quarterly summary @not-before=2026-09-01 @due=2026-09-05
- [x] Already done — skipped
```

Schedule tokens: `@not-before=` gates eligibility, `@due=` is an advisory
deadline that only affects ordering and reporting, `@every=` makes an item
recur (`weekday`, `1d`, `2w`, `mon,thu`). A completed recurring item stays
unchecked with its `@not-before=` advanced — that is what makes it recurring.
An unparseable schedule token blocks that item rather than being guessed at.

An indented task list under an item is **your** breakdown and is used verbatim;
the model is only asked to supply acceptance checks for steps that lack one.

See `todo.example.md`, and `specs/02-functional-spec.md` §Stage 1 for the full
metadata grammar.

## Security posture

- Runs the agent CLI with its unattended flag — that bypasses the **agent**
  permission layer, not an OS sandbox. Run it inside a sandbox with no push
  credentials in the environment.
- `network` is **not** a default capability; `curl`, `wget`, `ssh`, `scp` are on
  a standing deny list; `git push` and `gh` are hard-denied in every dispatched
  argv.
- Every subprocess is argv with `shell=False`. No shell-string interpolation.
- Secrets come from the environment or a git-ignored `.env`, never the repo.

## Licence

Apache-2.0.
