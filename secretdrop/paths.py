"""Destination allowlisting.

The only thing standing between a link and an arbitrary file on the host, so it
refuses anything it cannot prove is safe rather than trying to sanitise it.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path, PurePosixPath


class PathRejected(ValueError):
    """The destination is not an allowed place to write a secret."""


FORBIDDEN_CHARS = re.compile(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def check_text(value: str, what: str, limit: int = 200) -> str:
    """Refuse control and bidi characters that forge logs or spoof the page."""
    if not value.strip():
        raise PathRejected(f"{what} is empty")
    if len(value) > limit:
        raise PathRejected(f"{what} is longer than {limit} characters")
    if FORBIDDEN_CHARS.search(value):
        raise PathRejected(f"{what} contains a control or direction-override character")
    return value


def _expand(value: str) -> str:
    return os.path.expanduser(value)


def _resolve_existing_ancestors(path: Path) -> Path:
    """Resolve symlinks on the longest existing prefix, keep the rest literal.

    `Path.resolve()` on a non-existent path still resolves the parts that do
    exist, which is what we want: a symlinked parent directory cannot be used to
    point the final write outside the allowlist.
    """
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        # A symlink loop or a too-deep chain. Either way, not somewhere to write.
        raise PathRejected(f"cannot resolve destination: {exc}") from exc


def _matches(resolved: Path, pattern: str) -> bool:
    pattern_parts = PurePosixPath(pattern).parts
    path_parts = resolved.parts
    if len(pattern_parts) != len(path_parts):
        return False
    return all(fnmatch.fnmatchcase(p, pat) for p, pat in zip(path_parts, pattern_parts))


def resolve_destination(dest: str, allowlist: list[str]) -> Path:
    """Return the absolute destination path, or raise `PathRejected`.

    Checks, in order: no `..` anywhere, absolute after `~` expansion, symlinks
    resolved on every existing ancestor, and the result matches an allowlist
    entry segment by segment so a `*` never crosses a `/`.
    """
    check_text(dest, "destination", limit=4096)

    expanded = _expand(dest)
    raw = Path(expanded)

    if ".." in raw.parts:
        raise PathRejected("destination contains '..'")
    if not raw.is_absolute():
        raise PathRejected("destination must be an absolute path")

    resolved = _resolve_existing_ancestors(raw)
    if ".." in resolved.parts:
        raise PathRejected("destination contains '..' after resolution")

    patterns = [_expand(entry) for entry in allowlist]
    for pattern in patterns:
        if not PurePosixPath(pattern).is_absolute():
            continue
        if _matches(resolved, pattern):
            return resolved

    raise PathRejected("destination is not in the allowlist")
