<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Changelog

All notable changes to jumar are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No release has been published yet. Everything in the README's Status table —
`plan`, `run`, `resume`, `report`, `status`, `schedule`, `doctor`, `--json`
output, the verification stack, and the scheduling/backoff behaviour — is
built, tested, and merged on `main`, and will form the first release
(**0.1.0**).

Added since then: `[jumar.harness.profiles.<name>]` tables and the
`--harness-profile` flag on `plan`, `run`, `doctor`, `status` and
`schedule add`/`show` — a second model line-up lives in the same config file
as the first instead of a duplicate `jumar.toml` that drifts on `todo_path`
and `capabilities`. `doctor` now probes the resolved model for every stage
rather than the top-level `[harness] model` alone.

Security since then: the Claude harness denied `git push` and `gh` with
`--disallowedTools` text patterns, and a real run showed
`bash -c 'git push origin main'` and `echo 'git push origin main' | sh` both
reaching the remote. A fail-closed `PreToolUse` hook now parses every Bash
command the agent issues and refuses those spellings and the rest of the
family; the patterns stay as defence in depth.

Fixed since then: a judge `pass` was taken on trust. Its reason must now quote
text present in the artefact the judge was shown, and must not be identical to
an earlier attempt's reason for the same subtask; otherwise the pass is
downgraded to `inconclusive`. Both tests are mechanical, with no second model
call.

Fixed since then: the judge scraped the first `{` from its reply and accepted
any shape, so an empty reason could pass and an out-of-enum verdict silently
became `failed`. The reply is now pinned by a JSON schema, sent to Claude as
`--json-schema` and to the `openai` harness as a strict `response_format`, and
revalidated locally; anything that breaks it, and any judge run that did not
finish, is `inconclusive`. An endpoint that rejects the schema is never
retried without it.

Fixed since then: an absent precondition is now reported as unchecked
rather than passed or failed. `doctor` marks such probes `skip`
("Unchecked, because …"): no scheduler binary, no schedule module, no
`base_url` for the speed probe, and a crontab that could not be read, which the
legacy-schedule probe used to report as `ok`. A `judge` check whose artefact is
missing or empty fails without a model call, and one whose artefact cannot be
read is inconclusive, instead of the model being shown "Artefact not found".

Fixed since then: a subtask whose agent hit its step cap, timed out or was
truncated was still verified, so an artefact it half wrote, or one an earlier
attempt left behind, could pass the check and tick the box. Each attempt now
records how it ended (`termination`), only a clean ending is checked, and an
item whose last attempt was cut off fails with `unclean_termination`.

Fixed since then: `doctor` reported a missing `crontab` binary as FAIL and
exited non-zero on machines that simply have no cron (containers, cloud VMs).
It is now a WARN that names launchd agents and `systemd --user` timers as
alternatives. A crontab that exists but cannot be read is still a FAIL.

Renamed from GetStuffDone (`gsd`) to **jumar** on 2026-08-11, before anything
was published under the old name.

[Unreleased]: https://github.com/justinmclean/jumar/commits/main
