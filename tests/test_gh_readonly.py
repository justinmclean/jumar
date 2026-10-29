# SPDX-License-Identifier: Apache-2.0
"""jumar-gh: read-only gh commands pass, everything else is refused."""

from __future__ import annotations

import pytest

from jumar.config import Config, is_allowed
from jumar.gh_readonly import is_read_only
from jumar.harness import _CLAUDE_HARD_DENY_TOOLS, _CLAUDE_NO_NETWORK_TOOLS

READ_ONLY = [
    ["api", "repos/apache/iggy/issues/4631"],
    ["api", "--paginate", "repos/apache/iggy/issues/4631/timeline?per_page=100"],
    ["api", "-H", "Accept: application/vnd.github+json", "repos/apache/iggy/pulls/4670"],
    ["api", "repos/apache/iggy/pulls/4670", "-q", ".title"],
    ["api", "-X", "GET", "search/issues", "--jq", ".items"],
    ["api", "--method=GET", "repos/apache/iggy"],
    ["api", "graphql", "-f", "query={ viewer { login } }"],
    ["api", "graphql", "-F", "number=4402", "-f", "query=query($number: Int!) { x }"],
    ["issue", "view", "4631", "-R", "apache/iggy", "--comments"],
    ["issue", "list", "-R", "apache/iggy", "--state", "open"],
    ["pr", "view", "4670", "--json", "title,body"],
    ["pr", "diff", "4670"],
    ["pr", "checks", "4670"],
    ["search", "prs", "--repo", "apache/iggy", "--author", "hubcio"],
    ["run", "view", "123", "--log"],
    ["repo", "view", "apache/iggy"],
    ["label", "list", "-R", "apache/iggy"],
]

WRITES = [
    [],
    ["api"],
    ["api", "-X", "POST", "repos/apache/iggy/issues/1/comments"],
    ["api", "--method", "PATCH", "repos/apache/iggy/issues/1"],
    ["api", "-XDELETE", "repos/apache/iggy/issues/1/labels/bug"],
    ["api", "repos/apache/iggy/issues/1/comments", "-f", "body=hi"],
    ["api", "repos/apache/iggy/issues/1/comments", "--raw-field=body=hi"],
    ["api", "repos/apache/iggy/issues/1/comments", "-Fbody=hi"],
    ["api", "repos/apache/iggy/issues/1/comments", "--input", "body.json"],
    ["api", "-X", "GET", "repos/x/y", "-X"],
    ["api", "graphql", "-f", "query=mutation { addComment(input: {}) { clientMutationId } }"],
    ["api", "graphql", "-F", "query=@mutation.graphql"],
    ["api", "graphql"],
    ["api", "graphql", "--input", "q.json"],
    ["issue", "close", "4631"],
    ["issue", "edit", "2517", "--add-label", "postponed"],
    ["issue", "comment", "4670", "--body", "hi"],
    ["issue"],
    ["pr", "merge", "4670"],
    ["pr", "review", "4670", "--approve"],
    ["pr", "checkout", "4670"],
    ["run", "rerun", "123"],
    ["run", "download", "123"],
    ["release", "create", "v1"],
    ["repo", "clone", "apache/iggy"],
    ["label", "create", "x"],
    ["auth", "token"],
    ["secret", "list"],
    ["search"],
]


@pytest.mark.parametrize("args", READ_ONLY)
def test_read_only_commands_pass(args: list[str]) -> None:
    assert is_read_only(args) is True


@pytest.mark.parametrize("args", WRITES)
def test_writes_and_unknown_commands_are_refused(args: list[str]) -> None:
    assert is_read_only(args) is False


def test_policy_allows_read_only_jumar_gh_by_default() -> None:
    assert is_allowed(["jumar-gh", "pr", "view", "4670"], Config()) is True
    assert is_allowed(["/usr/local/bin/jumar-gh", "api", "repos/apache/iggy"], Config()) is True


def test_policy_refuses_jumar_gh_writes() -> None:
    assert is_allowed(["jumar-gh", "issue", "close", "4631"], Config()) is False
    assert is_allowed(["jumar-gh", "api", "-X", "POST", "repos/x/y/issues"], Config()) is False


def test_gh_itself_stays_hard_denied() -> None:
    assert is_allowed(["gh", "pr", "view", "4670"], Config()) is False
    assert "Bash(gh:*)" in _CLAUDE_HARD_DENY_TOOLS


def test_jumar_gh_disabled_without_network() -> None:
    assert "Bash(jumar-gh:*)" in _CLAUDE_NO_NETWORK_TOOLS
