# SPDX-License-Identifier: Apache-2.0
"""Human-facing run progress, written to stderr.

``gsd run`` is otherwise silent between the gate and the final summary: the
stages do not print, and the agent subprocess is captured rather than streamed.
On a long item that is indistinguishable from a hung process.

Two rules shape this module:

* **stderr only.** stdout carries the report summary and, under ``--json``, a
  single JSON document. Progress must never contaminate it.
* **quiet when nobody is watching.** Suppressed under ``--json`` and when the
  stream is not a TTY, so scheduled and piped runs stay silent.

Every method is a no-op when disabled, so call sites need no conditionals.
"""

from __future__ import annotations

import pathlib
import sys
from typing import IO, Any


class Progress:
    """Emits one line per stage transition. No-op unless enabled."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        verbose: bool = False,
        stream: IO[str] | None = None,
    ) -> None:
        self._enabled = enabled
        self._verbose = verbose
        self._stream: IO[str] = stream if stream is not None else sys.stderr
        self._open_line = False

    # -- construction --------------------------------------------------

    @classmethod
    def for_run(
        cls,
        *,
        json_output: bool = False,
        verbose: bool = False,
        stream: IO[str] | None = None,
        force: bool | None = None,
    ) -> Progress:
        """Build a reporter with the default enable rule.

        Enabled when NOT ``--json`` and the stream is a TTY. ``force``
        overrides the whole rule (tests pass ``force=True``; there is no TTY
        under pytest's capture, so without it every progress test would pass
        vacuously).
        """
        out: IO[str] = stream if stream is not None else sys.stderr
        if force is not None:
            enabled = force
        else:
            enabled = (not json_output) and bool(getattr(out, "isatty", lambda: False)())
        return cls(enabled=enabled, verbose=verbose, stream=out)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # -- internals -----------------------------------------------------

    def _write(self, text: str) -> None:
        if not self._enabled:
            return
        self._stream.write(text)
        self._stream.flush()

    def _close_line(self) -> None:
        if self._open_line:
            self._write("\n")
            self._open_line = False

    # -- events --------------------------------------------------------

    def item_started(self, text: str, subtask_count: int) -> None:
        self._close_line()
        self._write(f"→ {text}  ({subtask_count} subtask(s))\n")

    def subtask_started(self, index: int, total: int, description: str) -> None:
        """``index`` is 0-based; displayed 1-based."""
        self._close_line()
        self._write(f"[{index + 1}/{total}] {description} … ")
        self._open_line = True
        if self._verbose:
            self._close_line()

    def subtask_skipped(self, index: int, total: int, description: str) -> None:
        self._close_line()
        self._write(f"[{index + 1}/{total}] {description} … already passed (skipped)\n")

    def agent_transcript(self, transcript_path: str | None) -> None:
        """Echo the agent's captured output for this attempt. Verbose only.

        The harness runs with ``capture_output=True``, so nothing is available
        while the agent works; ``execute()`` writes the captured text to an
        artefact file and this reads it back afterwards. That is echo, not a
        live stream — see the module note in the work item. A missing or
        unreadable transcript is skipped silently: progress output must never
        be able to fail a run.
        """
        if not self._verbose or not transcript_path:
            return
        try:
            text = pathlib.Path(transcript_path).read_text(errors="replace")
        except OSError:
            return
        if not text.strip():
            return
        self._close_line()
        for line in text.rstrip("\n").splitlines():
            self._write(f"    │ {line}\n")

    def verifying(self, kind: Any) -> None:
        self._write(f"verifying ({kind}) … ")

    def check_failed(self, verdict: Any, summary: str | None = None) -> None:
        """Report a non-passing check at the moment it happens.

        Without this the terminal goes straight from ``verifying (file) …`` to
        ``repairing …``, which never says the check failed and never says why —
        the reason sits unprinted in the result until the whole repair budget
        is spent. Emit it here, then let ``repairing()`` open the next line.
        """
        detail = f" — {summary}" if summary else ""
        self._write(f"{verdict}{detail}\n")
        self._open_line = False

    def repairing(self, budget: int) -> None:
        """Announce that the bounded repair loop is starting."""
        self._write(f"  repairing (budget {budget}) …\n")
        self._open_line = False

    def repair_attempt(self, attempt_no: int, budget: int, description: str = "") -> None:
        """Open the line for one repair attempt. ``attempt_no`` is 1-based."""
        self._close_line()
        suffix = f" {description}" if description else ""
        self._write(f"  attempt {attempt_no}/{budget}{suffix} … ")
        self._open_line = True

    def repair_result(self, verdict: Any, summary: str | None = None) -> None:
        """Close a repair attempt's line with its verdict."""
        detail = f" — {summary}" if summary and str(verdict) != "passed" else ""
        self._write(f"{verdict}{detail}\n")
        self._open_line = False

    def verdict(self, verdict: Any, summary: str | None = None) -> None:
        detail = f" — {summary}" if summary and str(verdict) != "passed" else ""
        self._write(f"{verdict}{detail}\n")
        self._open_line = False

    def item_finished(self, outcome: str) -> None:
        self._close_line()
        self._write(f"→ {outcome}\n")
