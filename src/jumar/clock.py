# SPDX-License-Identifier: Apache-2.0
"""Run-level clock: the single source of the eligibility ``now``.

Nothing outside this module (and ``journal.py``, which stamps audit records)
may call ``datetime.now()`` or ``datetime.utcnow()`` directly.
``tests/test_cli.py`` enforces this statically.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from jumar.config import Config


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


def make_run_id(now: datetime, *, _suffix: str | None = None) -> str:
    """Generate a sortable run id from *now*: ``YYYYMMDD-HHMM-<4 hex chars>``.

    Parameters
    ----------
    now:
        The run's eligibility instant (from :func:`capture_now`).
    _suffix:
        Injectable 4-character suffix for tests.  If omitted, 4 random hex
        chars are generated via :mod:`secrets`.
    """
    dt = now.astimezone(UTC)
    suffix = _suffix if _suffix is not None else secrets.token_hex(2)
    return f"{dt.strftime('%Y%m%d-%H%M')}-{suffix}"


def make_session_id(*, _value: str | None = None) -> str:
    """Generate a unique session id for one item's execution session.

    The returned value is used with the harness ``--resume`` flag so the
    agent can continue the same conversation across subtasks.  Inject
    ``_value`` in tests to get a deterministic id.
    """
    if _value is not None:
        return _value
    return secrets.token_hex(16)


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
