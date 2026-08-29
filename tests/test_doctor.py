# SPDX-License-Identifier: Apache-2.0
"""Tests for jumar doctor checks (src/jumar/doctor.py)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from jumar.config import CommandPolicy, Config, HarnessConfig
from jumar.doctor import (
    CheckStatus,
    DoctorCheck,
    DoctorReport,
    format_report,
    run_doctor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**kwargs: object) -> Config:
    """Build a Config with sensible defaults, overriding any supplied keys."""
    defaults: dict[str, object] = dict(
        todo_path="todo.md",
        harness=HarnessConfig(agent="python3", model=""),
        commands=CommandPolicy(allow=("python3", "pytest"), deny=("curl",)),
    )
    defaults.update(kwargs)
    return Config(**defaults)  # type: ignore[arg-type]


def _find(report: DoctorReport, name: str) -> DoctorCheck:
    """Return the first check whose name starts with *name*."""
    for c in report.checks:
        if c.name == name or c.name.startswith(name + "."):
            return c
    raise KeyError(f"No check named {name!r} in report: {[c.name for c in report.checks]}")


# ---------------------------------------------------------------------------
# Config checks
# ---------------------------------------------------------------------------


def test_config_valid_is_ok() -> None:
    report = run_doctor(_cfg())
    check = _find(report, "config")
    assert check.status == CheckStatus.ok


def test_config_invalid_max_subtasks() -> None:
    config = _cfg(max_subtasks=0)
    report = run_doctor(config)
    check = _find(report, "config.max_subtasks")
    assert check.status == CheckStatus.fail
    assert "max_subtasks" in check.message
    assert "1" in check.message


def test_config_invalid_max_repairs() -> None:
    config = _cfg(max_repairs=-1)
    report = run_doctor(config)
    check = _find(report, "config.max_repairs")
    assert check.status == CheckStatus.fail
    assert "max_repairs" in check.message


def test_config_invalid_timeout() -> None:
    config = _cfg(subtask_timeout_s=0)
    report = run_doctor(config)
    check = _find(report, "config.subtask_timeout_s")
    assert check.status == CheckStatus.fail
    assert "subtask_timeout_s" in check.message


def test_config_invalid_timezone() -> None:
    config = _cfg(timezone="Not/ATimezone")
    report = run_doctor(config)
    check = _find(report, "config.timezone")
    assert check.status == CheckStatus.fail
    assert "Not/ATimezone" in check.message


def test_config_multiple_bad_fields_all_reported() -> None:
    config = _cfg(max_subtasks=0, max_repairs=-1)
    report = run_doctor(config)
    names = {c.name for c in report.checks}
    assert "config.max_subtasks" in names
    assert "config.max_repairs" in names


# ---------------------------------------------------------------------------
# Config source checks
# ---------------------------------------------------------------------------


def test_config_source_defaults_is_warn_and_names_defaults() -> None:
    # _cfg() builds a Config directly, so config_source keeps its own default —
    # DEFAULT_CONFIG_SOURCE, i.e. "no file found".
    config = _cfg()
    report = run_doctor(config)
    check = _find(report, "config.source")
    assert check.status == CheckStatus.warn
    assert "no config file found" in check.message.lower()
    assert config.todo_path in check.message
    assert config.harness.agent in check.message
    assert str(len(config.commands.allow)) in check.message


def test_config_source_jumar_toml_is_ok_and_names_path(tmp_path: Path) -> None:
    jumar_toml = tmp_path / "jumar.toml"
    config = _cfg(config_source=str(jumar_toml))
    report = run_doctor(config)
    check = _find(report, "config.source")
    assert check.status == CheckStatus.ok
    assert str(jumar_toml) in check.message


def test_config_source_pyproject_is_ok_and_names_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    config = _cfg(config_source=f"{pyproject} [tool.jumar]")
    report = run_doctor(config)
    check = _find(report, "config.source")
    assert check.status == CheckStatus.ok
    assert str(pyproject) in check.message
    assert "[tool.jumar]" in check.message


def test_config_source_defaults_does_not_contribute_to_exit_status(tmp_path: Path) -> None:
    """Defaults are a warn, not a fail — doctor should still exit 0 overall
    when everything else (harness, todo) is fine."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Task\n")
    config = _cfg(harness=HarnessConfig(agent="python3", model=""), todo_path=str(todo))
    report = run_doctor(config)
    source_check = _find(report, "config.source")
    assert source_check.status == CheckStatus.warn
    fail_checks = [c for c in report.checks if c.status == CheckStatus.fail]
    assert fail_checks == [], f"unexpected FAILs: {fail_checks}"


def test_cli_doctor_warns_when_no_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Do something\n")

    from jumar.cli import main

    rc = main(["doctor", "--todo", str(todo)])
    out = capsys.readouterr().out
    assert "config.source" in out
    assert "warn" in out.lower()
    assert "no config file found" in out.lower()
    # A warn-only doctor run still exits 0 when nothing else fails.
    assert rc == 0


def test_cli_doctor_ok_when_jumar_toml_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Do something\n")
    (tmp_path / "jumar.toml").write_text(
        '[jumar]\ntodo_path = "todo.md"\n\n[jumar.harness]\nagent = "python3"\nmodel = ""\n'
    )

    from jumar.cli import main

    rc = main(["doctor", "--todo", str(todo)])
    out = capsys.readouterr().out
    assert str((tmp_path / "jumar.toml").resolve()) in out
    assert rc == 0


# ---------------------------------------------------------------------------
# Harness checks
# ---------------------------------------------------------------------------


def test_harness_found_on_path() -> None:
    # python3 is guaranteed to be on PATH in the test environment
    config = _cfg(harness=HarnessConfig(agent="python3", model=""))
    report = run_doctor(config)
    check = _find(report, "harness")
    assert check.status == CheckStatus.ok
    assert "python3" in check.message


def test_harness_not_found_on_path() -> None:
    # When the user has an explicit config file and the agent is not on PATH,
    # the check must be a hard FAIL.
    config = _cfg(
        harness=HarnessConfig(agent="no-such-jumar-binary-zzz", model=""),
        config_source="/some/jumar.toml",
    )
    report = run_doctor(config)
    check = _find(report, "harness")
    assert check.status == CheckStatus.fail
    assert "no-such-jumar-binary-zzz" in check.message
    assert "PATH" in check.message


def test_harness_not_found_on_path_is_warn_when_no_config_file() -> None:
    # When running on built-in defaults (no config file), a missing harness
    # binary is a warning — the user has not yet created a jumar.toml.
    config = _cfg(harness=HarnessConfig(agent="no-such-jumar-binary-zzz", model=""))
    # _cfg() leaves config_source at DEFAULT_CONFIG_SOURCE
    report = run_doctor(config)
    check = _find(report, "harness")
    assert check.status == CheckStatus.warn
    assert "no-such-jumar-binary-zzz" in check.message


def test_harness_fail_contributes_to_exit_status() -> None:
    config = _cfg(
        harness=HarnessConfig(agent="no-such-jumar-binary-zzz", model=""),
        config_source="/some/jumar.toml",
    )
    report = run_doctor(config)
    assert report.exit_status == 1


# ---------------------------------------------------------------------------
# Harness checks — in-process "openai" harness: /models probe
# ---------------------------------------------------------------------------


class _ModelsHandler(BaseHTTPRequestHandler):
    """Serves a canned /v1/models response for exactly one test's lifetime."""

    served_ids: tuple[str, ...] = ()

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        body = json.dumps({"data": [{"id": model_id} for model_id in self.served_ids]}).encode(
            "utf-8"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence stdlib's default access log
        pass


@pytest.fixture
def models_server() -> Iterator[Callable[[tuple[str, ...]], str]]:
    """Start a stub /models server; the fixture value builds it, returning base_url (…/v1)."""
    servers: list[HTTPServer] = []

    def _make(served_ids: tuple[str, ...]) -> str:
        handler = type("_Handler", (_ModelsHandler,), {"served_ids": served_ids})
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        servers.append(server)
        return f"http://{host}:{port}/v1"

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()


def test_openai_harness_missing_base_url_is_fail() -> None:
    config = _cfg(harness=HarnessConfig(agent="openai", model="qwen"))
    report = run_doctor(config)
    check = _find(report, "harness")
    assert check.status == CheckStatus.fail
    assert "base_url" in check.message


def test_openai_harness_server_absent_is_fail() -> None:
    """No server listening at all — GET /models must fail closed, not hang."""
    config = _cfg(
        harness=HarnessConfig(agent="openai", model="qwen", base_url="http://127.0.0.1:1/v1")
    )
    report = run_doctor(config)
    check = _find(report, "harness")
    assert check.status == CheckStatus.fail
    assert "Could not reach" in check.message


def test_openai_harness_reachable_and_model_served_is_ok(
    models_server: Callable[[tuple[str, ...]], str],
) -> None:
    base_url = models_server(("qwen2.5-coder-32b",))
    harness = HarnessConfig(agent="openai", model="qwen2.5-coder-32b", base_url=base_url)
    report = run_doctor(_cfg(harness=harness))
    check = _find(report, "harness")
    assert check.status == CheckStatus.ok
    assert "qwen2.5-coder-32b" in check.message


def test_openai_harness_reachable_but_model_not_served_is_warn(
    models_server: Callable[[tuple[str, ...]], str],
) -> None:
    base_url = models_server(("some-other-model",))
    harness = HarnessConfig(agent="openai", model="qwen2.5-coder-32b", base_url=base_url)
    report = run_doctor(_cfg(harness=harness))
    check = _find(report, "harness")
    assert check.status == CheckStatus.warn
    assert "qwen2.5-coder-32b" in check.message
    assert "some-other-model" in check.message


# ---------------------------------------------------------------------------
# Allow-list checks
# ---------------------------------------------------------------------------


def test_allowlist_clean_is_ok() -> None:
    config = _cfg(commands=CommandPolicy(allow=("python3",), deny=("curl",)))
    report = run_doctor(config)
    check = _find(report, "allowlist")
    assert check.status == CheckStatus.ok


def test_allowlist_overlap_is_warn() -> None:
    config = _cfg(commands=CommandPolicy(allow=("curl", "python3"), deny=("curl",)))
    report = run_doctor(config)
    check = _find(report, "allowlist.overlap")
    assert check.status == CheckStatus.warn
    assert "curl" in check.message


def test_allowlist_empty_is_fail() -> None:
    config = _cfg(commands=CommandPolicy(allow=(), deny=()))
    report = run_doctor(config)
    check = _find(report, "allowlist.empty")
    assert check.status == CheckStatus.fail
    assert "empty" in check.message.lower()


def test_allowlist_empty_contributes_to_exit_status() -> None:
    config = _cfg(commands=CommandPolicy(allow=(), deny=()))
    report = run_doctor(config)
    assert report.exit_status == 1


def test_allowlist_overlap_does_not_set_exit_status_alone() -> None:
    # Overlap is WARN only; harness=python3 ok, config ok, todo missing but
    # we need a valid todo for this test to isolate allowlist.
    tmp = Path(__file__).parent  # some directory that exists, but not a todo file
    config = _cfg(
        commands=CommandPolicy(allow=("curl", "python3"), deny=("curl",)),
        todo_path=str(tmp / "_no_such_todo_for_overlap_test.md"),
    )
    report = run_doctor(config)
    # The todo check will FAIL (missing), but allowlist.overlap is WARN only
    overlap = _find(report, "allowlist.overlap")
    assert overlap.status == CheckStatus.warn


# ---------------------------------------------------------------------------
# Todo file checks
# ---------------------------------------------------------------------------


def test_todo_missing_is_fail(tmp_path: Path) -> None:
    config = _cfg(todo_path=str(tmp_path / "nonexistent.md"))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert check.status == CheckStatus.fail
    assert "not found" in check.message.lower()


def test_todo_missing_names_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"
    config = _cfg(todo_path=str(missing))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert str(missing) in check.message


def test_todo_parseable_is_ok(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Do something productive\n")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert check.status == CheckStatus.ok
    assert "1" in check.message


def test_todo_with_warnings_is_warn(tmp_path: Path) -> None:
    # An item with an unparseable schedule token produces a parse warning.
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Recurring task @due=not-a-real-date\n")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    check = _find(report, "todo")
    # bad schedule → item is blocked, ingest emits a parse warning or the item status
    # reflects the issue; either way the check should be warn or fail (not ok).
    assert check.status in (CheckStatus.warn, CheckStatus.fail)


def test_todo_empty_file_is_ok(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    check = _find(report, "todo")
    assert check.status == CheckStatus.ok


def test_todo_fail_contributes_to_exit_status(tmp_path: Path) -> None:
    config = _cfg(
        todo_path=str(tmp_path / "gone.md"),
        harness=HarnessConfig(agent="python3", model=""),
    )
    report = run_doctor(config)
    assert report.exit_status == 1


# ---------------------------------------------------------------------------
# Schedule check
# ---------------------------------------------------------------------------


def test_schedule_module_absent_is_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """When schedule module is absent, check is warn, not fail."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "jumar.schedule":
            raise ImportError("schedule not available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    todo = Path(__file__).parent.parent / "tests" / "test_doctor.py"  # any real file
    config = _cfg(todo_path=str(todo))

    # Reimport doctor to pick up the monkeypatched import, then call _check_schedule
    from jumar.doctor import _check_schedule

    check = _check_schedule(config)
    assert check.status == CheckStatus.warn
    assert "schedule" in check.message.lower()


def test_schedule_module_absent_does_not_fail_run(tmp_path: Path) -> None:
    """A missing schedule module produces a warn, not a fail."""
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] One task\n")
    config = _cfg(todo_path=str(todo))

    from jumar.doctor import _check_schedule

    check = _check_schedule(config)
    # Should be warn (module absent) or ok (if schedule is present and empty)
    assert check.status in (CheckStatus.warn, CheckStatus.ok)


# ---------------------------------------------------------------------------
# DoctorReport aggregation
# ---------------------------------------------------------------------------


def test_report_exit_status_zero_when_all_ok(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Task\n")
    config = _cfg(todo_path=str(todo), harness=HarnessConfig(agent="python3", model=""))
    report = run_doctor(config)
    # Only possible FAILs: harness or todo; both are ok here.
    # Ignore schedule warn.
    fail_checks = [c for c in report.checks if c.status == CheckStatus.fail]
    assert fail_checks == [], f"unexpected FAILs: {fail_checks}"
    assert report.exit_status == 0


def test_report_exit_status_one_when_any_fail(tmp_path: Path) -> None:
    config = _cfg(todo_path=str(tmp_path / "missing.md"))
    report = run_doctor(config)
    assert report.exit_status == 1


def test_format_report_contains_check_names(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Task\n")
    config = _cfg(todo_path=str(todo))
    report = run_doctor(config)
    text = format_report(report)
    assert "jumar doctor" in text
    assert "todo" in text
    assert "harness" in text


def test_format_report_shows_fail_label(tmp_path: Path) -> None:
    config = _cfg(todo_path=str(tmp_path / "absent.md"))
    report = run_doctor(config)
    text = format_report(report)
    assert "FAIL" in text


def test_format_report_shows_ok_label(tmp_path: Path) -> None:
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] A task\n")
    config = _cfg(todo_path=str(todo), harness=HarnessConfig(agent="python3", model=""))
    report = run_doctor(config)
    text = format_report(report)
    assert "ok" in text.lower()


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_cli_doctor_exits_nonzero_on_missing_todo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    from jumar.cli import main

    rc = main(["doctor", "--todo", str(tmp_path / "no_such.md")])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_cli_doctor_exits_zero_with_valid_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    todo = tmp_path / "todo.md"
    todo.write_text("- [ ] Write tests\n")

    # Write a jumar.toml that uses python3 as the harness (guaranteed on PATH).
    (tmp_path / "jumar.toml").write_text(
        '[jumar]\ntodo_path = "todo.md"\n\n[jumar.harness]\nagent = "python3"\nmodel = ""\n'
    )

    from jumar.cli import main

    rc = main(["doctor", "--todo", str(todo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "jumar doctor" in out


def test_cli_doctor_subcommand_is_known() -> None:
    from jumar.cli import build_parser

    args = build_parser().parse_args(["doctor"])
    assert args.command == "doctor"


# ---------------------------------------------------------------------------
# Legacy (pre-rename) schedule markers
# ---------------------------------------------------------------------------


def test_legacy_findings_empty_when_clean() -> None:
    from jumar.doctor import _legacy_marker_findings

    assert _legacy_marker_findings("0 9 * * * /usr/bin/true\n", []) == []


def test_legacy_findings_reports_cron_marker() -> None:
    from jumar.doctor import _legacy_marker_findings

    text = '# gsd-meta: {"schedule_id":"abc"}\n0 9 * * * gsd run\n'  # legacy-name-ok
    findings = _legacy_marker_findings(text, [])
    assert len(findings) == 1
    assert "1 crontab entry(ies)" in findings[0]


def test_legacy_findings_reports_launchd_plists() -> None:
    from jumar.doctor import _legacy_marker_findings

    plists = ["com.gsd.abc.plist", "com.gsd.def.plist"]  # legacy-name-ok
    findings = _legacy_marker_findings("", plists)
    assert len(findings) == 1
    assert "2 launchd agent(s)" in findings[0]
    assert "com.gsd.abc.plist" in findings[0]  # legacy-name-ok


def test_legacy_check_warns_on_cron_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jumar import doctor as _doctor

    class _Result:
        returncode = 0
        stdout = '# gsd-meta: {"schedule_id":"abc"}\n'  # legacy-name-ok

    monkeypatch.setattr(_doctor.subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(_doctor.Path, "home", staticmethod(lambda: tmp_path))
    check = _doctor._check_legacy_schedule_markers()
    assert check.status is _doctor.CheckStatus.warn
    assert "Pre-rename" in check.message


def test_legacy_check_ok_when_crontab_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jumar import doctor as _doctor

    def _raise(*a: object, **k: object) -> object:
        raise OSError("no crontab binary")

    monkeypatch.setattr(_doctor.subprocess, "run", _raise)
    monkeypatch.setattr(_doctor.Path, "home", staticmethod(lambda: tmp_path))
    check = _doctor._check_legacy_schedule_markers()
    assert check.status is _doctor.CheckStatus.ok


# ---------------------------------------------------------------------------
# harness.speed — a resident model vs one running from swap
# ---------------------------------------------------------------------------


class _CompletionHandler(BaseHTTPRequestHandler):
    """Answers /chat/completions with a canned usage block after a delay."""

    tokens: int = 16
    delay_s: float = 0.0

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.delay_s:
            time.sleep(self.delay_s)
        body = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"completion_tokens": self.tokens},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def completion_server() -> Iterator[Callable[[int, float], str]]:
    servers: list[HTTPServer] = []

    def _make(tokens: int, delay_s: float) -> str:
        handler = type("_H", (_CompletionHandler,), {"tokens": tokens, "delay_s": delay_s})
        server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        host, port = server.server_address[:2]
        return f"http://{host}:{port}/v1"

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()


def test_generation_speed_ok_when_the_model_is_resident(completion_server: Any) -> None:
    base_url = completion_server(16, 0.0)
    config = _cfg(harness=HarnessConfig(agent="openai", model="m", base_url=base_url))
    check = _find(run_doctor(config), "harness.speed")
    assert check.status == CheckStatus.ok
    assert "t/s" in check.message


def test_generation_speed_warns_when_the_model_is_crawling(completion_server: Any) -> None:
    """One token in half a second is 2 t/s — the signature of a model being
    read from disk because it does not fit in memory at its loaded context.

    `harness` above stays green throughout: the endpoint answers and the id is
    served. This is the only check that separates the two.
    """
    base_url = completion_server(1, 0.5)
    config = _cfg(harness=HarnessConfig(agent="openai", model="m", base_url=base_url))
    check = _find(run_doctor(config), "harness.speed")
    assert check.status == CheckStatus.warn
    assert "swap" in check.message


def test_generation_speed_skipped_for_subprocess_harnesses() -> None:
    """Only the in-process harness talks to an endpoint; there is nothing to
    time for a CLI harness, and inventing a number would be worse than none."""
    config = _cfg(harness=HarnessConfig(agent="python3", model=""))
    names = [c.name for c in run_doctor(config).checks]
    assert "harness.speed" not in names
