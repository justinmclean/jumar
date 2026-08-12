<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via
[GitHub Security Advisories](https://github.com/justinmclean/jumar/security/advisories/new)
— do not open a public issue. You should receive a response within a week.

Include the affected version or commit, a minimal reproduction, and any
relevant logs or run journal excerpts.

## Scope: what jumar promises (and what it does not)

Jumar dispatches agent processes to do real work on your machine, so its
security posture is explicit and worth reading before you run it:

- **The enforced boundary is send, not fetch.** Outbound-transmission vectors
  (`mail`, `mailx`, `sendmail`, `ssmtp`, `msmtp`, `ssh`, `scp`, `sftp`,
  `rsync`) are denied in every argv jumar dispatches, and `git push` / `gh`
  are hard-denied. Network *fetch* is allowed by default.
- **MCP server inheritance is disabled** for the Claude harness
  (`--strict-mcp-config`), so user-configured MCP servers cannot bypass the
  deny list.
- **The allow list is defence in depth, not a sandbox.** With an interpreter
  allowed and the network reachable, the real control is running jumar inside
  a container/VM with restricted egress and **no push credentials in the
  environment**. Please run it that way.
- Subtask environments are scrubbed (`GITHUB_TOKEN`, API keys, SSH agent
  socket removed), every subprocess is argv-only (`shell=False`), and
  verifier contexts are always fresh.

Reports that demonstrate a bypass of the send boundary, the argv deny list,
the environment scrub, or the verifier-isolation guarantee are especially
valuable.

## Supported versions

Only the latest release receives fixes. Before the first release, fixes land
on `main`.
