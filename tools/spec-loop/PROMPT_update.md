<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

You are running the **update** beat of the spec-driven loop for GetStuffDone.
Specs can fall behind the code when functionality lands the normal way (a
regular commit, not through this loop). This beat brings the specs back in sync
with reality. It is the inverse of `plan`: `plan` finds code missing against
specs; `update` finds **functionality missing against specs** and back-fills
the specs.

Context to load first:

- `AGENTS.md` — operational rules.
- `specs/*` — the current functional description.
- The actual code: `src/getstuffdone/`, `tests/`, the CLI.
- The appended **Repository snapshot** block from the runner — the first
  routing map before opening full files.

Steps:

1. Read the **Repository snapshot** to identify likely spec/source
   relationships. It is a routing aid, not proof; confirm with a code search
   before recording something as present or absent.
2. **Create a uniquely-named sync branch off the integration base**, then
   switch to it: `git checkout -b "sync-specs-$(date +%Y%m%d-%H%M%S)"`. A fresh
   branch every run keeps each sync as its own reviewable PR. Note the exact
   name — you will print it below. Never commit the sync to the base.
3. Inventory the code (parallel subagents are fine): every module under
   `src/getstuffdone/`, what it does, and its tests under `tests/`. Map each
   module back to the pipeline stage in `specs/02-functional-spec.md` it
   implements.
4. Diff that inventory against `specs/`:
   - **New functionality with no spec** → add it to the relevant existing spec,
     or author a new topic-named spec (no number prefix) following the format
     in `specs/README.md` (if present) grounded in the real code.
   - **Drifted spec** → a spec whose *Where it lives*, *Behaviour & contract*,
     or `status` no longer matches the code → update it to match reality
     (e.g. a `proposed` stage that now has shipped code becomes `done`).
   - **Removed functionality** → mark the spec or move it to a `Known gaps`
     note; do not silently delete history.
5. Update any spec index (`specs/README.md`, `README.md`) if areas were added
   or renamed.
6. `git add -A` then `git commit` with subject
   `docs(spec): sync specs with contributed functionality` and a
   `Generated-by: <agent> (<model>)` trailer (actual agent/model, e.g.
   `Claude (Sonnet 4.5)`) — do not hardcode either.

Then STOP. Do NOT push, do NOT open a PR. Print the human-run commands
(substitute `<sync-branch>` with the exact name you created in step 2):

```text
git push -u origin <sync-branch>
gh pr create --web --base main --head <sync-branch> \
  --title "Sync specs with contributed functionality" --body-file <body>
```

Rules:

- **Edit specs only.** This beat changes `specs/` and the spec indexes. It must
  NOT change any source or test — it documents reality, it does not alter it.
- Confirm with a code search before recording something as present or absent.
  Do not invent behaviour the code does not have.
- When code implements an acceptance criterion, cite the AC id in the spec
  update rather than rewriting the criterion to match the code. If the code
  contradicts an AC, that is drift to report, not a spec to bend.
