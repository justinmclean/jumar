<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Using Jumar

Worked examples, command reference, config reference. Every transcript below is
**real output** from the current build, not illustration. Where a command is not
yet wired, this document says so rather than showing what it will one day print.

See [README.md](README.md) for what the tool is and why. See
[`specs/02-functional-spec.md`](specs/02-functional-spec.md) for the contract
each stage must satisfy.

---


## 1. Set up a todo file

`todo.md` is git-ignored by default, so your real list never lands in a commit.

```bash
cp todo.example.md todo.md
```

```markdown
# My list

Context lines like this one are attached to the item below them and are passed
to the agent as background — they are never treated as work.

- [ ] Add a --json flag to the export script @id=export-json @priority=1 @capability=write_fs
- [ ] Update the README install section @depends=export-json
      - [ ] Rewrite the install steps for the new flag
      - [ ] Check every command in the README actually runs
- [ ] Rotate the backup logs @every=weekday
- [ ] Draft the quarterly summary @not-before=2026-09-01 @due=2026-09-05
- [x] This one is already done and will be skipped
```

---

## 2. See what it would do — `jumar plan --dry-run`

The safest command in the tool. It ingests the file, applies both eligibility
gates, and prints the decision. **No agent is called and nothing is executed.**

```console
$ jumar plan --dry-run
Run:   20260811-0748-5dc9
Todo:  todo.md  (4 pending)
Now:   2026-08-11T07:48:43.028260+00:00

Selected:  Add a --json flag to the export script
  id:           export-json
  capabilities: network, read_fs, run_commands, write_fs

Deferred (1):
  - 'Draft the quarterly summary'  [eligible at 2026-08-31T14:00:00+00:00]

Blocked (1):
  - 'Update the README install section'  [depends on unfinished export-json]
```

Reading that output:

- **Selected** — the one item that would be worked. Exactly one, always.
- **Deferred** — time-ineligible (`@not-before=` in the future). Not a failure;
  the instant it becomes eligible is shown.
- **Blocked** — dependency-ineligible (`@depends=` on an unfinished item).
- **Now** — the run's single eligibility instant, captured once and journalled.
  A long run cannot see time move underneath it.

Point it at another file with `--todo`:

```bash
jumar plan --dry-run --todo ~/work/todo.md
```

Every invocation writes `runs/<run-id>/journal.jsonl`, even a dry run. The
journal is the audit trail, and it is what `resume` and `report` read.

### Bad schedule tokens block one item, not the run

An unparseable date is refused rather than guessed at — guessing is exactly the
wrong instinct for unattended work:

```console
$ jumar plan --dry-run --todo bad.md
warning: Line 1: bad schedule token: @due='next': Cannot parse date/datetime: 'next'
Run:   20260811-0748-7d5d
Todo:  bad.md  (1 pending)
Now:   2026-08-11T07:48:46.890802+00:00

Selected:  Fine item
  id:           fine-item-334aeee7
  capabilities: network, read_fs, run_commands, write_fs
```

The malformed item is dropped from consideration; the rest of the file still
parses and the run still exits 0.

### Errors are actionable, not tracebacks

```console
$ jumar plan --dry-run --todo nope.md
error: Todo file not found: nope.md
$ echo $?
1
```

---

## 3. Run an item — `jumar run`

The full pipeline: select → decompose → gate → execute → verify → repair →
complete → report, one item per invocation, under the single-flight lock.
Progress goes to stderr (one line per stage; `--verbose` echoes the agent's
captured output), and `--json` turns stdout into a single machine-readable
document.

One startup gate is worth knowing about, because it is the one flag
combination with no safe resolution — auto-approving would defeat the gate,
auto-declining would silently drop every item:

```console
$ jumar run --approve --non-interactive
error: --approve and --non-interactive are mutually exclusive: --approve waits
for a human response, but --non-interactive forbids blocking reads. Remove one
of these flags.
$ echo $?
2
```

Modes (`specs/02-functional-spec.md` §Stage 4):

| Flag | Behaviour |
|---|---|
| *(none)* | `auto` — plan, then execute straight through. |
| `--dry-run` | Print the plan and its checks, journal it, execute nothing. |
| `--approve` | Print the plan and wait for `y/n` before executing. |
| `--non-interactive` | Never block on a prompt. Required for scheduled runs; makes `manual` checks resolve `inconclusive` rather than hanging. |
| `--verbose` | Echo the agent's captured output after each attempt (stderr). |
| `--json` | Machine-readable stdout; progress suppressed. |

---

## 4. What happens during a run — reading the live output

`jumar run` emits per-stage progress to **stderr**. Standard output carries only
the run summary (and, under `--json`, a single machine-readable document).
Progress is suppressed when the stream is not a TTY and when `--json` is
active, so scheduled and piped runs stay silent.

```console
$ jumar run
→ Write two marker files
  planning … (one agent call, up to 12 subtasks)
  planned 2 subtask(s)
[1/2] Write marker.txt containing OK … verifying (file) … passed
[2/2] Write second.txt containing OK … verifying (file) … passed
→ done
Run: 1 done
```

Reading the output left to right:

- **`→ <item>`** — the item selected by `select`. Exactly one item per
  invocation.
- **`planning …`** — the model is decomposing the item into subtasks and
  authoring a check for each. One agent call. On a long item this is minutes
  of silence — the blank terminal is expected.
- **`[N/T] <description> …`** — subtask N of T has been sent to the agent.
  Nothing is trusted yet; this line stays open until the verifier closes it.
- **`verifying (<kind>) …`** — the verifier is running the check
  **independently of what the agent reported**. The agent's claim does not
  appear on this line; only the verifier's result does.
- **`passed` / `failed — <reason>` / `inconclusive — <reason>`** — what the
  verifier confirmed. A `passed` result closes the subtask; anything else
  enters the repair loop.
- **`repairing (budget N) …`** — the check was not passed; the repair loop
  begins with N attempts remaining.
- **`attempt K/N … <verdict>`** — one repair attempt, followed by its
  verdict. The same original check is always re-run after each repair —
  a repair may never substitute a different check.
- **`→ done`** / **`→ failed`** — the item's final outcome, emitted after all
  subtasks have been processed (or after the run is aborted by exhausted
  repairs or a halt).

### Agent claims and verifier evidence — the trust gap

The agent's own "I'm done" is captured in the journal as `agent_claim` on the
`attempt_finished` event. It is never the verdict. The verifier runs as a
separate invocation — a subprocess, a file check, or an independent judge call
— with no access to the agent's reasoning chain. The gap between the two is
the product's trust boundary: **a passed subtask means the check passed**, not
that the agent's description sounded plausible.

To see claim and evidence side by side:

```bash
python3 -m json.tool < runs/20260808-0851-a3f9/journal.jsonl
```

Each `attempt_finished` event carries `agent_claim` (what the agent said it
did) and `files_touched` (git status before vs. after). The `verify_result`
event that follows carries the verifier's `verdict` and `evidence` dict —
the concrete artefact that proved the claim, or the concrete observation that
falsified it.

### Verbose mode — echo the agent's output after each initial attempt

```bash
jumar run --verbose
```

Under `--verbose`, the agent's captured stdout is echoed to stderr after each
initial subtask execution, prefixed with `│`:

```console
[2/3] Document the flag in the README …
    │ I've updated the README. The --json flag is now documented in
    │ section 5 with a usage example.
  verifying (file) … failed — pattern '--json' not found in README.md
```

The transcript is what the agent returned, unchanged. The verifier result
below it is independent.

Note: the `--verbose` echo applies to the **initial** execution of each
subtask. Repair attempts show only the verdict line — the repair agent's
transcript is written to `runs/<run-id>/artifacts/` and must be read from
there if needed.

### The repair cycle

When a check returns `failed` or `inconclusive`, the repair loop re-runs the
agent with the failing check and its evidence in the prompt:

```console
[2/3] Document the flag in the README … verifying (file) … failed — pattern '--json' not found in README.md
  repairing (budget 2) …
  attempt 1/2 … passed
```

When the budget is exhausted without a pass:

```console
[2/3] Document the flag in the README … verifying (file) … failed — pattern '--json' not found in README.md
  repairing (budget 2) …
  attempt 1/2 … failed — pattern '--json' not found in README.md
  attempt 2/2 … failed — pattern '--json' not found in README.md
repairs exhausted — pattern '--json' not found in README.md
→ failed
```

`max_repairs` from config controls the budget (default 2). On exhaustion the
item fails at that subtask. If `halt_on_fail = true`, the entire run stops;
otherwise the next eligible item is selected.

### Status — item-centric view across all runs

While `jumar report` answers "what happened to this run?", `jumar status` answers
"where does each item stand right now?":

```console
$ jumar status
Status for: todo.md

  [ELIGIBLE        ] Add a --json flag to the export script
    last: run=20260808-0851-a3f9  at=2026-08-08T08:51:11.729401+00:00  outcome=failed
    failing subtask: Document the flag in the README
    check kind: file
  [NEVER-ATTEMPTED ] Update the README install section
  [DEFERRED        ] Draft the quarterly summary  [eligible: 2026-09-01T00:00:00+00:00]
  [BLOCKED         ] Fetch the invoice report
  [PARKED          ] Rotate the backup logs  (paused: auto-failures)  [failures: 3]
  [DONE            ] This one is already done
```

`jumar status` scans every journal under `runs/` and merges the results with the
current todo file. One row per item.

| State | Meaning |
|---|---|
| `ELIGIBLE` | Pending, no blocking gate. Last-run info shown below if any. |
| `NEVER-ATTEMPTED` | Eligible, but no journal has ever touched this item. |
| `DEFERRED` | `@not-before=` is in the future; next eligibility instant shown. |
| `BLOCKED` | Unsatisfied `@depends=` edge. |
| `PARKED` | `@paused=` token present; the item is skipped until you remove it. |
| `DONE` | Checkbox checked in the file. |

For a failed item, `status` shows which subtask failed and what check kind —
so you know whether to inspect a file, a command's exit code, or a judge
artefact before retrying.

`jumar status --json` emits the same data as a JSON document.

### Putting it together — the next-action loop

```
jumar run                         # attempt the next eligible item
 ↓ if it reports "failed":
jumar status                      # which item, which subtask, which check kind
jumar report <run-id>             # full evidence trail for that run
 ↓ once you have addressed the cause:
jumar resume <run-id> --retry-failed    # re-enter from the first unverified subtask
```

All three views read the same journal files. `jumar resume --retry-failed` never
re-executes a subtask that already carries a `passed` verdict — verified side
effects are not repeated.

---

## 5. Read a run — `jumar report`

```console
$ jumar report demo-run
Run: 1 failed

Failed: Add a --json flag to the export script
  Reason: check_failed
  Subtask 2: Document the flag in the README
  Check (file): README documents --json
  Evidence: pattern '--json' not found in README.md

Report written to: runs/demo-run/report.md
$ echo $?
1
```

That is the shape of every failure report: **which subtask, which check, what
the evidence was**. Not "the agent reported a problem".

The written `runs/<run-id>/report.md`:

```markdown
# Run Report: demo-run

- **Started:** 2026-08-08T09:00:00+00:00
- **Timezone:** Australia/Sydney
- **Todo file:** todo.md
- **Mode:** auto

## Summary

| Status | Count |
|--------|-------|
| Done | 0 |
| Failed | 1 |

## Failed Items

### Add a --json flag to the export script
- Item ID: `add-a-json-flag-to-the-export-script-a67187c5`
- Failure: `check_failed`
- Failed at subtask 2: Document the flag in the README
  - Check (`file`): README documents --json
  - Evidence: pattern '--json' not found in README.md
```

**Exit status is the contract:** `0` if every selected item completed —
*including a run where nothing was eligible* — and `1` if any item failed or was
inconclusive. Deferred and blocked items do not fail a run. A scheduled run with
nothing to do is a success and must not page anyone.

`jumar run --until-empty` repeats the same verified pipeline in one foreground
process until no eligible item remains. It holds the normal single-flight lock
for the whole pass, re-ingests the todo file after each completed item, and only
starts the next item after the previous one has finished. If an item fails, that
item is skipped for the rest of the pass so independent work can continue; its
dependents remain blocked because the failed item is not treated as complete.
The command exits `1` if any attempted item failed.

```bash
jumar report <run-id> --runs-dir /path/to/runs   # non-default runs directory
```

---

## 6. Resume an interrupted run — `jumar resume`

Replays `runs/<run-id>/journal.jsonl` and continues from the first subtask
**without a `passed` verdict**.

```bash
jumar resume 20260809-1543-a3f9        # full id: YYYYMMDD-HHMM-<4 hex>
jumar resume 2026 --retry-failed       # any unambiguous prefix works
jumar resume latest                    # most recently *started* run
```

Run ids sort chronologically under `ls runs/` and are typeable: anywhere a run
id is accepted (`resume`, `report`), an unambiguous prefix resolves (an
ambiguous one errors, naming the candidates) and `latest` resolves to the run
with the most recent journalled start. Old uuid-named runs still resolve.

By default resume continues an **interrupted** run; an item the journal
records as terminally failed is reported, not re-run. `--retry-failed`
reopens a failed item at its first unverified subtask — the command that makes
"fix the bug, then retry the run" possible without redoing verified work.

Two properties worth understanding, because they are what make resume safe:

- **Verified work is never redone.** A subtask with a `passed` verdict in the
  journal is skipped, not re-executed. Side effects are not repeated.
- **Eligibility does not shift.** Resume reuses the *original* `now` from the
  journal's `run_started` event rather than reading the clock again. An item
  that was deferred when the run started stays deferred on resume — otherwise
  the resumed run would be a different run.

Resume writes `report.md` on completion and returns the same exit-status
contract as `jumar report`.

---

## 7. Scheduling

Two independent mechanisms. Keeping them separate matters.

### Item eligibility — works today

Expressed in the todo line, honoured by `select`:

```markdown
- [ ] Rotate the backup logs @every=weekday
- [ ] Draft the quarterly summary @not-before=2026-09-01 @due=2026-09-05
- [ ] Reconcile invoices @every=2w @priority=1
```

| Token | Accepted forms |
|---|---|
| `@not-before=` | `2026-09-01`, `2026-09-01T09:00` |
| `@due=` | same |
| `@every=` | `weekday`, `1d`, `2w`, `mon,thu` |

A bare date resolves to 00:00 in the configured timezone. Everything downstream
is timezone-aware UTC.

**Ordering among eligible items:** overdue first (most overdue first), then
`@priority` ascending, then `@due` ascending, then file order.

**Overdue changes queue position and nothing else.** It never shortens a plan,
loosens a check, or skips verification — enforced by a test asserting `due` is
unread by `decompose`, `execute`, `verify` and `repair`.

**Recurrence on completion:** a completed `@every=` item stays *unchecked* with
its `@not-before=` advanced in place; the rest of the line is byte-identical. A
missed occurrence advances to the single next occurrence after `now`, never a
backlog of catch-up runs. A *failed* recurring item's schedule is left untouched.

### Recurring runs — `jumar schedule`

Writes a `cron`/`launchd` entry that invokes `jumar run --non-interactive` and
exits — there is no resident daemon:

```bash
jumar schedule show "0 9 * * 1-5" --todo ~/todo.md            # print, install nothing
jumar schedule add "0 9 * * 1-5" --todo ~/todo.md --dry-run   # ditto
jumar schedule add "0 9 * * 1-5" --todo ~/todo.md
jumar schedule list
jumar schedule remove <schedule-id>
```

Installed entries are wrapped in `jumar <schedule-id>` markers and `remove` only
ever deletes inside its own block — your existing crontab lines are never
reformatted. Overlapping firings are safe: the single-flight lock (`lock.py`)
makes the second firing exit 0 with `already_running`, and a stale lock from a
crashed run is reclaimed with a journalled note.

---

## 8. Configuration

`jumar.toml` in the working directory, or a `[tool.jumar]` table in
`pyproject.toml`. CLI flags override config. Current effective defaults:

```toml
[jumar]
todo_path           = "todo.md"
timezone            = "Australia/Sydney"   # defaults to the system zone
max_subtasks        = 12
max_repairs         = 2
max_consecutive_failures = 3   # @failed= threshold before auto-parking
subtask_timeout_s   = 900
halt_on_fail        = false
commit_on_complete  = false
capabilities        = ["read_fs", "write_fs", "run_commands", "network"]
evidence_head_bytes = 4000
allow_unrestricted_harness = false

[jumar.harness]
agent = "claude"       # claude | codex | cursor | gemini | opencode | kiro | openai
model = "sonnet"       # the default for every stage
# base_url and api_key_env only matter when agent = "openai" — see §Local models below.

# Per-stage overrides. Valid stage tables: decompose, execute, judge.
# Omitted keys inherit [jumar.harness]; an unknown stage name or key is a
# startup error, never a silently ignored typo.
[jumar.harness.decompose]
model = "opus"

[jumar.harness.judge]
model = "opus"

# Named alternative harnesses. Same shape as [jumar.harness] itself: scalar
# keys and/or stage tables. Selected per run with --harness-profile NAME;
# without the flag the base [jumar.harness] above applies unchanged.
[jumar.harness.profiles.heavy.execute]
model = "opus"

[jumar.commands]
allow = ["python3", "pytest", "ruff", "git", "make", "curl", "wget"]
deny  = ["mail", "mailx", "sendmail", "ssmtp", "msmtp", "ssh", "scp", "sftp", "rsync"]
```

Notes that matter:

- **`deny` beats `allow`.** An `argv[0]` outside `allow` is refused *before* the
  process is spawned, with `capability_denied`.
- **`@harness=<name>` routes one item to one profile.** Which model an item
  needs is a property of the item, not of the invocation: an extraction task
  and a task turning on judgement want different models, and `--until-empty`
  cannot express that from the command line. Put `@harness=heavy` on the item
  and every run picks it up. An explicit `--harness-profile` still wins, so
  you can force a whole pass onto one line-up to compare. A name with no
  matching profile fails at ingest, before any model call — never a silent
  fall-back to the default.
- **Harness profiles are for a second model line-up, not a second config file.**
  `jumar run --harness-profile heavy` layers `[jumar.harness.profiles.heavy]`
  over `[jumar.harness]`; everything else in the file — `todo_path`,
  `capabilities`, the command policy — is shared, so it cannot drift between
  the fast and slow line-ups the way two config files do. Resolution runs
  highest-first: `[…profiles.NAME.<stage>]` → `[…profiles.NAME]` →
  `[jumar.harness.<stage>]` → `[jumar.harness]`. The selected profile's
  top-level therefore outranks the base file's per-stage overrides: a profile
  that says `model = "opus"` means *every* stage, not "opus except where the
  base file named something else". An unknown profile name is a startup
  error, never a silent fall-back to the base harness, and every defined
  profile is shape-checked at load — a typo in one you are not running today
  still fails now rather than on the run that finally selects it.
  `jumar schedule add` bakes the flag into the installed command, so a
  scheduled run pins its line-up the same way. `jumar doctor` names the
  active profile on its `config.source` line.
- **The boundary is send, not fetch.** `network` *is* a default capability and
  `curl`/`wget` are allowed — an agent that cannot read a primary source
  fabricates it instead. The deny list is the outbound-transmission vectors
  (mail, ssh, scp, rsync, …), and `git push` / `gh` are hard-denied in every
  dispatched argv. None of this is a sandbox: run jumar in a container/VM with
  restricted egress and no push credentials (see README §Security posture).
- **Choosing per-stage models:** put the strong model where the leverage is —
  **decompose high** (a bad plan poisons every later stage), **execute cheap**
  (the bulk of calls, each one verified anyway), **judge independent** (ideally
  a different model from the executor, so the verdict is not the executor
  grading its own homework).
- **`max_consecutive_failures`**: each failed run advances `@failed=N` on the
  item's line; at the threshold `@paused=auto-failures` is appended and the
  item is parked — `jumar status` shows it, and you unpark by deleting the
  `@paused=` token from the line.
- **`commit_on_complete`** commits a finished item on a per-item branch. It
  never pushes and never opens a PR.
- **`timezone`** resolves bare dates like `@due=2026-09-05`. Set it explicitly if
  you don't want the system zone.

### Local models

`agent = "openai"` is not a CLI wrapper like the other six — jumar drives the
tool-calling loop itself, in process, against any OpenAI-compatible
`/chat/completions` endpoint (LM Studio, llama.cpp's server, vLLM, …). No
extra dependency: it's `urllib.request` from the standard library.

```toml
[jumar.harness]
agent        = "openai"
model        = "qwen2.5-coder-32b"                 # the exact id from /v1/models — see below
base_url     = "http://192.168.1.8:1234/v1"        # your LM Studio / server address
api_key_env  = "LMSTUDIO_API_KEY"                  # omit if the endpoint needs no key

# Keep judge on a frontier model even when execute runs locally — see the
# caveat below.
[jumar.harness.judge]
agent = "claude"
model = "sonnet"
```

- **Getting the exact model id.** LM Studio (and most local servers) name the
  model differently than the file you loaded. Ask the server:
  `curl http://192.168.1.8:1234/v1/models` and copy the `id` field verbatim
  into `model = "…"` — a mismatch is not a startup error, it's a request the
  server rejects or silently answers with whatever it has loaded.
- **What `jumar doctor` checks.** For every other harness, `doctor` looks for a
  binary on `PATH`. For `agent = "openai"` there is no binary — instead it GETs
  `{base_url}/models` and reports `fail` if the endpoint is unreachable, `warn`
  if it's reachable but a configured `model` isn't in the served list, `ok`
  otherwise. It probes the *resolved* model for every stage — including the
  one the selected `--harness-profile` supplies — not just the top-level
  `[jumar.harness] model`, which once every stage carries an override is an id
  nothing ever calls.
- **`api_key_env` names an environment variable**, not the key itself — the key
  never goes in `jumar.toml`. Leave it unset for a local server that takes no
  auth; the request is then sent with no `Authorization` header at all.
- **The tool loop is jumar's own**, not the model's CLI: `read_file`,
  `write_file` and `run_command` are offered as tools and each call is checked
  against the subtask's `Capability` set and the `[jumar.commands]` allow/deny
  policy before anything runs — the same checks the execute stage and every
  verifier already apply, in-process, so `allow_unrestricted_harness` is never
  needed for `openai`.
- **The caveat that matters:** a local model grading its own (or a same-tier
  local model's) execute output is a weaker check than the design assumes — the
  judge verifier's whole premise is an *independent* verdict. Keep
  `[jumar.harness.judge]` on a frontier model (as above) even when `execute`
  runs against a local server; only fall back to a fully local judge if you've
  separately validated that model's adversarial-verifier behaviour.

---

## 9. Check kinds

Every subtask carries exactly one `Check`. A subtask whose plan has no check is
rejected at planning time — the decomposition is retried once, then the item
fails with `unverifiable_plan`.

| Kind | Passes when | Evidence recorded |
|---|---|---|
| `command` | argv exits with the expected status (default 0) | argv, exit code, captured stdout/stderr |
| `file` | path exists and matches the required pattern | path, size, sha256, matched excerpt |
| `absence` | path or glob is gone | path, resolved glob |
| `judge` | an independent agent returns `pass` | verdict, reason, artefacts shown |
| `manual` | a human confirms at the prompt | response, timestamp |

Rules worth knowing before you write a `@check=` by hand:

- A `command` check is **argv, never a shell string**. No metacharacter
  interpolation, anywhere.
- `judge` requires a `rationale` explaining why no executable check is possible.
  It is prompted adversarially — its default answer is fail, and "looks
  reasonable" is explicitly not evidence. An unparseable response is
  `inconclusive`, not a pass.
- `manual` under `--non-interactive` yields `inconclusive`, never an auto-pass.
- A check that **cannot run** — missing binary, unreachable path — is
  `inconclusive`. That is not a pass, and it routes to repair like a failure.

Adding a sixth kind requires only a new module plus one `register()` call in
`verify/__init__.py`; nothing in `execute.py` changes. That claim is now backed
by four real additions rather than one.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `error: Todo file not found: X` | `todo_path` wrong, or you're in the wrong directory | `jumar plan --dry-run --todo /absolute/path` |
| `warning: bad schedule token` | Unparseable `@not-before=` / `@due=` / `@every=` | Use `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`; that item is skipped until fixed |
| `Nothing eligible to work on.` | Everything deferred, blocked, or done | The printed *next eligible* instant tells you when to come back |
| `error: --approve and --non-interactive are mutually exclusive` | Both flags passed | Drop one; scheduled runs must use `--non-interactive` |
| `capability_denied` | Subtask wants authority the item wasn't granted | Add `@capability=` to the item, or fix the plan |
| `unverifiable_plan` | Decomposition produced a subtask with no real check | Usually a too-vague todo line — split it, or supply your own indented subtasks |
| `plan_too_long` | Decomposition exceeded `max_subtasks` | The item is mis-sized. Split it. Raising the cap is treating the symptom |
| `ImportError: cannot import name 'UTC'` | Python 3.10 or older | Needs 3.11+ |
| `jumar: command not found` | Not installed | `make install` |

Inspect any run directly — the journal is append-only JSON lines:

```bash
ls runs/
python3 -m json.tool < runs/<run-id>/journal.jsonl   # per-line
cat runs/<run-id>/report.md
column -t -s $'\t' < runs/index.tsv                  # run index: id, started, item, status
```

`runs/index.tsv` is a cache for fast `latest` resolution — one line per run,
appended at `run_started`, status updated at `run_finished`. The journals stay
authoritative: a missing or corrupt index falls back to a full journal scan,
and uuid-era run directories without an index row still resolve by prefix.

---

## 11. Command reference

```
jumar [--version] [--help]
jumar plan   [--dry-run] [--todo PATH] [--json]
jumar run    [--dry-run | --approve] [--non-interactive] [--until-empty] [--verbose] [--json] [--todo PATH]
jumar resume RUN_ID [--retry-failed] [--runs-dir DIR]     # RUN_ID: full id, prefix, or 'latest'
jumar report RUN_ID [--runs-dir DIR] [--json]             # ditto
jumar status [--todo PATH] [--runs-dir DIR] [--json]
jumar schedule (add | list | remove | show) …
jumar doctor
```

Exit statuses: `0` success (including nothing eligible), `1` a run failed or was
inconclusive, `2` a usage or not-implemented error.

`jumar plan` without `--dry-run` currently exits 2 — the full plan path is wired
to `--dry-run` only.
