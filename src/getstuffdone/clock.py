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


def stamp(*, _now: datetime | None = None) -> str:
    """Return the current instant as an ISO-8601 UTC string, for audit fields.

    An audit stamp (``Plan.created_at``, ``Attempt.started_at``) is NOT the
    run's eligibility ``now``: it records when a thing actually happened, so it
    must not be pinned to run start.  It still routes through this module so
    ``clock.py`` remains the only wall-clock reader.

    Parameters
    ----------
    _now:
        Injectable override for tests.  Pass a UTC-aware datetime to pin it.
    """
    moment = _now if _now is not None else datetime.now(UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
