# SPDX-License-Identifier: Apache-2.0
"""Pin the code a command check runs from the working tree (W23).

A ``command`` check such as ``["python3", "check.py"]`` runs whatever
``check.py`` says, from a directory the executing agent writes to. If the
agent wrote or edited that script, the verifier is agent-authored code: the
half of the system meant to be independent is grading with the executor's own
pen.

So every file a command check would *execute* from inside the working tree is
pinned by SHA-256 when the plan is accepted, before any subtask runs, and the
command verifier refuses to spawn the check unless each of them still exists
with the same hash. A program that lives outside the tree (``/usr/bin/grep``,
the interpreter jumar runs under) is not the agent's to change and is not
pinned.

"Executed" means:

- ``argv[0]`` written as a path (``./verify.sh``, ``scripts/check``), or a bare
  name that PATH resolves to a file inside the tree (an agent-planted
  ``./bin/grep``, or a ``.venv/bin/pytest`` that was there all along);
- an interpreter's script operand: ``python3 check.py``, ``bash verify.sh``,
  ``node check.js``, ``perl t.pl``, ``ruby t.rb``;
- ``python3 -m NAME`` when ``NAME`` resolves to a module file inside the tree.

Inline programs (``python3 -c``, ``perl -e``) carry their code in the argv the
plan journalled, so there is nothing further to pin.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePath

from ..models import Check

_PYTHONS = ("python", "python3")
_SCRIPT_INTERPRETERS = frozenset(
    {"bash", "sh", "zsh", "dash", "ksh", "node", "perl", "ruby", "php", "lua", "Rscript"}
)
# Interpreter options that take the next argv element as their value.
_VALUE_OPTIONS = frozenset({"-W", "-X", "-I", "-r", "--require", "-o", "-O"})
# Options after which the rest of argv is inline code or arguments, not a file.
_INLINE_OPTIONS = frozenset({"-c", "-e", "-E", "--eval", "-p", "--print"})


def _is_python(name: str) -> bool:
    return any(
        name == p or (name.startswith(p + ".") and name[len(p) + 1 :].isdigit()) for p in _PYTHONS
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _resolve(token: str, cwd: Path) -> Path:
    return Path(token) if Path(token).is_absolute() else cwd / token


def executed_files(check: Check, cwd: Path) -> list[str]:
    """In-tree files *check* would execute, relative to *cwd* where possible.

    Includes files that do not exist yet, so a caller can tell "the subtask
    would have to write its own verifier" from "nothing in the tree runs".
    """
    argv = list(check.command or ())
    if not argv:
        return []
    found: list[str] = []

    def add(path: Path) -> None:
        if not _inside(path, cwd):
            return
        try:
            shown = str(path.resolve().relative_to(cwd.resolve()))
        except (OSError, ValueError):
            shown = str(path)
        if shown not in found:
            found.append(shown)

    program = argv[0]
    if "/" in program:
        add(_resolve(program, cwd))
    else:
        located = shutil.which(program)
        if located is not None:
            add(Path(located))

    name = PurePath(program).name
    if _is_python(name) or name in _SCRIPT_INTERPRETERS:
        args = argv[1:]
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in _INLINE_OPTIONS:
                break
            if _is_python(name) and arg == "-m" and i + 1 < len(args):
                module = args[i + 1].replace(".", "/")
                for candidate in (cwd / f"{module}.py", cwd / module / "__main__.py"):
                    if candidate.exists():
                        add(candidate)
                        break
                break
            if arg in _VALUE_OPTIONS:
                i += 2
                continue
            if arg.startswith("-") and arg != "-":
                i += 1
                continue
            if arg != "-":
                add(_resolve(arg, cwd))
            break
    return found


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _sidecar(run_dir: Path, subtask_id: str) -> Path:
    return run_dir / "artifacts" / f"{subtask_id.replace('#', '-')}-pinned-plan.json"


def pin_plan(check: Check, *, subtask_id: str, cwd: Path, run_dir: Path) -> dict[str, str | None]:
    """Pin the in-tree files *check* executes; written once, so a resume keeps it."""
    sidecar = _sidecar(run_dir, subtask_id)
    try:
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    except (OSError, ValueError):
        pass
    pins = {rel: _sha256(cwd / rel) for rel in executed_files(check, cwd)}
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(pins, indent=1, sort_keys=True), encoding="utf-8")
    return pins


def unpinned_program(
    check: Check, *, subtask_id: str, cwd: Path, run_dir: Path
) -> dict[str, str | None] | None:
    """Why the check must not run, or None when every in-tree program is pinned and intact.

    Returns the evidence for a refusal: ``error`` is ``check_program_not_pinned``
    (no plan-time record, or the file did not exist then) or
    ``check_program_modified`` (its hash changed since).
    """
    executed = executed_files(check, cwd)
    if not executed:
        return None
    try:
        pins = json.loads(_sidecar(run_dir, subtask_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pins = None
    for rel in executed:
        current = _sha256(cwd / rel)
        pinned = pins.get(rel) if isinstance(pins, dict) else None
        if pinned is None:
            return {
                "error": "check_program_not_pinned",
                "path": rel,
                "pinned_sha256": None,
                "current_sha256": current,
            }
        if current != pinned:
            return {
                "error": "check_program_modified",
                "path": rel,
                "pinned_sha256": pinned,
                "current_sha256": current,
            }
    return None


def unwritten_program(check: Check, cwd: Path) -> str | None:
    """A plan-time reason to reject *check*: it runs an in-tree file that does not exist yet."""
    for rel in executed_files(check, cwd):
        if not (cwd / rel).exists():
            return (
                f"the check runs {rel!r}, which does not exist yet, so the subtask would"
                " write its own verifier and the check would run agent-authored code."
                " Check the outcome with kind=file / kind=absence and a pattern, or run"
                " a program that is already in the repository."
            )
    return None
