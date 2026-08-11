<!-- SPDX-License-Identifier: Apache-2.0
     https://www.apache.org/licenses/LICENSE-2.0 -->

# Security Policy

## Reporting a vulnerability

Please report security issues privately by emailing the maintainer, Justin Mclean.
If you do not already have a direct address, open a GitHub issue asking for a
private security contact without including exploit details.

Include the affected version or commit, a minimal reproduction, and any relevant
logs or run journal excerpts. You should receive an acknowledgement within seven
days.

## Scope

Jumar's built-in command policy is defence in depth, not a sandbox. The intended
security boundary is the execution environment: run unattended or scheduled work
inside a container or virtual machine with restricted egress and no push
credentials.

The project treats the send boundary seriously. Fetching primary sources is
allowed by default, while obvious outbound transmission tools such as mail, SSH,
SCP, rsync, `git push`, and `gh` are denied for dispatched commands.
