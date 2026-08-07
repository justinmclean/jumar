<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

You are running the **plan** beat of the spec-driven loop for GetStuffDone.
Plan only — do NOT implement anything and do NOT commit code.

Context to load first:

- `AGENTS.md` — operational rules (repo map, validation commands, branch rules).
- `specs/*` — the functional description of the product.
- `IMPLEMENTATION_PLAN.md` (if present; may be stale).
- The appended **Repository snapshot** block from the runner — the first
  routing map before opening full files.
- The appended **Open pull-request context** and **Local work-item branches**
  blocks. Built-but-unpushed work items live in the branch list, not the PRs.

Steps:

1. Read the **Repository snapshot** to identify the likely relevant specs,
   source modules, and tests. It is a routing aid, not proof: before recording
   a gap or declaring one closed, confirm with a code search or file read.
2. Study each spec in `specs/` and compare it against the actual code it
   describes under `src/getstuffdone/` and `tests/`. You may use parallel
   subagents for reading. Do NOT assume something is missing — confirm first.
3. Read the **Open pull-request context** and **Local work-item branches**.
   Treat both as in-flight work. If an apparent gap is already substantially
   covered by an open PR (including drafts) or already built on a local
   work-item branch, do not add it as a planned work item.
4. For each spec, identify the **gaps**: an acceptance criterion (AC*) with no
   code or no test, a pipeline stage described but not implemented, a
   documented behaviour that drifted from the code, a missing negative test, a
   `Known gaps` item. Each gap is a candidate work item. Acceptance criteria
   are numbered in `specs/02-functional-spec.md` — name the AC ids a work item
   closes.
5. Rewrite `IMPLEMENTATION_PLAN.md` as a prioritised list of work items. Each
   work item names: the change, the spec it serves, its **Validation** command,
   and a bare branch slug (`<slug>` — no `spec/` prefix, no numbers). Respect
   the phase ordering in `specs/04-technical-plan.md` (Phase 0 skeleton, then
   read-only path, then decompose+gate, then the execute/verify inner loop).
   **Never plan a work item that ships execution ahead of the verification it
   depends on** — if a phase's execute and verify would land separately, order
   them adjacently and say so in the item.
6. Do NOT plan work against a spec whose `status:` is `off`.

Rules:

- Plan only. No edits to source, tests, or specs. No commits in this beat.
- Keep the plan prioritised and concise; one work item = one branch = one PR.
- Do not duplicate in-flight work. If a stale plan item is now covered by an
  open PR or a local work-item branch, remove it or mark it in-flight rather
  than leaving it available for the build beat.
- Prefer extending an existing module under `src/getstuffdone/` over adding a
  new ad-hoc one.
- Every work item needs one concrete **Validation** command (usually
  `make check`, plus the specific `pytest tests/test_<stage>.py` it adds) and
  the AC ids it closes. A work item with no check is not a work item.
