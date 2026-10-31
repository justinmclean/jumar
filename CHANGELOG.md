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

Fixed since then: `doctor` reported a missing `crontab` binary as FAIL and
exited non-zero on machines that simply have no cron (containers, cloud VMs).
It is now a WARN that names launchd agents and `systemd --user` timers as
alternatives. A crontab that exists but cannot be read is still a FAIL.

Renamed from GetStuffDone (`gsd`) to **jumar** on 2026-08-11, before anything
was published under the old name.

[Unreleased]: https://github.com/justinmclean/jumar/commits/main
