<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Contributing to Jumar

Thanks for your interest. Jumar is a small, spec-driven project with strong
opinions about verification — this page tells you what to expect and how to
land a change smoothly.

**Hand-written PRs are welcome.** The repo is usually built by its own
spec-loop (`tools/spec-loop/`), but that is the maintainer's workflow, not a
requirement for contributors. If you write the change yourself, you only need
the rules below.

## Getting set up

Python 3.11 or newer.

```bash
git clone https://github.com/justinmclean/jumar.git && cd jumar
make install      # editable install + dev tools (pytest, ruff, mypy)
make check        # ruff + mypy + pytest + build-loop fixture tests — what CI runs
```

If `make check` is green locally, CI will be green.

## Finding something to work on

- Issues labelled **good first issue** are small, well-bounded, and have a
  stated acceptance check.
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) is the live work list; the
  specs in [`specs/`](specs/) are the source of truth for behaviour.
- For anything non-trivial, **open an issue first**. Behaviour is defined by
  numbered acceptance criteria in `specs/02-functional-spec.md`, and a change
  that contradicts the spec will be declined no matter how clean the code is —
  agreeing on the criteria up front saves everyone time.

## The rules that get PRs merged

These come from [AGENTS.md](AGENTS.md), which is the operational rulebook for
this repo (written for agents, but binding on everyone):

- **One work item per branch, one branch per PR.** Don't bundle unrelated
  changes.
- **Every stage change needs tests, including refusal paths.** The negative
  cases — unverifiable plans, self-modified checks, ungranted capabilities,
  corrupt journals — *are* the product. A happy-path-only PR is incomplete.
- **Never weaken a check to make it pass.** Don't delete, skip, `xfail`, or
  loosen a test or acceptance criterion. If you believe a criterion is wrong,
  open an issue and say so — the maintainer decides.
- **Don't edit `specs/` in a feature PR.** Specs are owned by the human and
  the spec-sync process.
- **No `shell=True`, ever.** Every subprocess in the product is argv with
  `shell=False`.
- **No secrets in the repo**, and never commit a real `todo.md` or `runs/`
  directory.

## Commits

- Imperative subject describing the user-visible change.
- If an agent wrote the change, add the trailer
  `Generated-by: <agent> (<model>)` naming the actual agent and model. Don't
  add agent `Co-Authored-By:` trailers.

## Questions, bugs, security

- Questions and bug reports: open an issue — the bug template asks for
  `jumar --version`, `jumar doctor` output, and a `runs/<id>/journal.jsonl`
  excerpt, which usually make the problem diagnosable on the first read.
- Security issues: privately, via [SECURITY.md](SECURITY.md) — never a public
  issue.

By participating you agree to the [code of conduct](CODE_OF_CONDUCT.md).
Contributions are licensed under [Apache-2.0](LICENSE).
