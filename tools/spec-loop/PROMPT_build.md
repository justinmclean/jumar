<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

You are running the **build** beat of the spec-driven loop for GetStuffDone.
Implement exactly ONE work item, on its OWN branch.

Context to load first:

- `AGENTS.md` — operational rules (repo map, validation commands, branch +
  hard-limit rules).
- `IMPLEMENTATION_PLAN.md` — the prioritised work items.
- The appended **Repository snapshot** block from the runner — use it to route
  to the likely spec/source/test files before opening them.
- The appended **Open pull-request context** and **Local work-item branches**
  blocks from the runner. The loop never pushes, so a work item it already
  built shows up in the branch list, not the PR list.
- Only the spec(s) and source files relevant to the chosen work item — do not
  read the whole tree.

Steps:

1. Read the **Repository snapshot** as a routing aid (not proof; verify against
   the plan and real files before changing anything).
2. Read the **Open pull-request context** and **Local work-item branches**.
   Treat both as in-flight work. Pick the single highest-priority work item
   from `IMPLEMENTATION_PLAN.md` that is **not** already covered by an open PR
   and **not** already built as a local work-item branch. One only.
3. **Create its branch off the integration base**, then switch to it:
   `git checkout -b <slug>`, where `<slug>` is the work item's bare branch slug
   (no `spec/` or other prefix, e.g. `ingest-markdown-todos`). NEVER commit work
   to the integration base. One branch per work item.
4. Read only the relevant `specs/*.md` file(s) plus the `src/getstuffdone/` and
   `tests/` files the item touches. Confirm what already exists before writing —
   do not assume.
5. Implement the work item **completely** — no placeholders, no stubs, no
   `NotImplementedError` left behind. Follow `specs/03-data-model.md` for the
   shapes and `specs/04-technical-plan.md` for the architecture and the phase
   ordering. Every pipeline stage you add or change must ship or extend its
   `pytest` module under `tests/` (a stage without a test is incomplete), and
   that must include the **negative** cases named in the acceptance criteria —
   this product is a refusal machine; the paths where it says no are the
   product.
6. Run the work item's **Validation** command(s) — at minimum `make check`
   (ruff + mypy + pytest). Fix until they pass. **Do not commit on red.** If you
   cannot get it green, revert your changes, record the blocker as a note in
   `IMPLEMENTATION_PLAN.md`, and stop without committing.
7. Do **not** edit anything under `specs/` — specs are read-only to build
   iterations (see AGENTS.md). Progress and gap notes belong in
   `IMPLEMENTATION_PLAN.md`, and even there only when you are blocked.
8. `git add -A` then `git commit` with an imperative subject and a
   `Generated-by: <agent> (<model>)` trailer, where `<agent>`/`<model>` are the
   actual agent and model you are running as (e.g. `Claude (Sonnet 4.5)`) — do
   not hardcode either. **Never** add a `Co-Authored-By:` trailer for an agent.

Then STOP. Do NOT push and do NOT open a PR — that is the human's step. Print
the exact commands the human can run:

```text
git push -u origin <slug>
gh pr create --web --base main --head <slug> \
  --title "<subject>" --body-file <prepared-body>
```

Rules:

- One work item per iteration. Do not bundle.
- Do not duplicate in-flight work. If the top plan item is already covered by
  an open PR or an existing local work-item branch, skip it and take the next
  uncovered item. Checking local branches (not just open PRs) is what keeps the
  loop from rebuilding the same item every iteration.
- **Never weaken a check to make it pass.** Deleting, skipping, or loosening a
  test or an acceptance criterion to get green is the one failure mode this
  project cannot tolerate — in the build loop or in the product it builds. If a
  criterion looks wrong, note it in the plan and stop; the human decides.
- Never ship a stage that executes work without the verification it depends on
  (`specs/04-technical-plan.md` §Phases). Execute and verify land together.
- Never commit secrets. No API keys in the repo — env or a git-ignored `.env`.
- Single source of truth — no duplicate logic; extend existing modules under
  `src/getstuffdone/`.
