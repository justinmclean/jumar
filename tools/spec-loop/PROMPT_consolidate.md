<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

`IMPLEMENTATION_PLAN.md` has grown too long. Consolidate it without losing
planned work.

1. Read `IMPLEMENTATION_PLAN.md` in full.
2. In **What's been built**: collapse each completed item to a single concise
   line. The detail lives in the code and specs.
3. In **Work items (planned)**: keep every work item intact — these still guide
   future build beats. Do not remove or shorten planned work.
4. Remove redundant notes, stale caveats, and duplicates.
5. Rewrite the file. Aim for **under 300 lines** — comfortably below the
   consolidation trigger so the loop does not immediately re-consolidate. Shrink
   by collapsing the *What's been built* section only; **every planned work item
   is preserved**. If planned work alone still exceeds 300 lines, that is fine —
   do not pad, and never drop a work item to hit the number.
6. `git add -A` then
   `git commit -m "chore(spec-loop): consolidate implementation plan"` with a
   `Generated-by: <agent> (<model>)` trailer (actual agent/model, e.g.
   `Claude (Sonnet 4.5)`) — do not hardcode either.

Rules:

- Do not mark any planned work item as done.
- Do not remove any planned work item.
- Do not touch `specs/` or any source/test file.
- Commit only the plan file. Do not push or open a PR.
