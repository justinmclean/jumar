# SPDX-License-Identifier: Apache-2.0
"""Run-level clock: the single source of the eligibility ``now``.

Nothing outside this module (and ``journal.py``, which stamps audit records)
may call ``datetime.now()`` or ``datetime.utcnow()`` directly.
``tests/test_cli.py`` enforces this statically.
"""

from __future__ import annotations

from datetime import UTC, datetime

from getstuffdone.config import Config


def capture_now(config: Config, *, _now: datetime | None = None) -> datetime:
    """Return the run's single eligibility instant as a UTC-aware datetime.

    Parameters
    ----------
    config:
        Resolved configuration (unused today; reserved for future zone handling).
    _now:
        Injectable override for tests.  Pass a UTC-aware datetime to pin the
        clock without patching the module.
    """
    if _now is not None:
        return _now.astimezone(UTC)
    return datetime.now(UTC)
