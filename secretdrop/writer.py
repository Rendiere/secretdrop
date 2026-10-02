"""Writing a secret to its destination.

Every step assumes the directory could change underneath us. Each path
component is opened with `O_NOFOLLOW` relative to the previous one, the temp
file is created `O_EXCL` with a random name, and the mode is set on the file
descriptor before a single byte is written.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
from pathlib import Path

MODES = ("file", "env")
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FORBIDDEN_IN_VALUE = ("\x00",)
FORBIDDEN_IN_ENV_VALUE = ("\x00", "\n", "\r")

FILE_MODE = 0o600
DIR_MODE = 0o700


class ValueRejected(ValueError):
    """The pasted value cannot be written safely in this mode."""


def validate_key(key: str | None) -> str:
    if key is None or not KEY_PATTERN.match(key):
        raise ValueError("env mode needs a key matching [A-Za-z_][A-Za-z0-9_]*")
    return key


def validate_mode(mode: str, key: str | None) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if mode == "env":
        validate_key(key)


def validate_value(value: str, mode: str) -> str:
    """Return the value as it will be written, or raise `ValueRejected`."""
    value = value.strip()
    if not value:
        raise ValueRejected("value is empty")
    forbidden = FORBIDDEN_IN_ENV_VALUE if mode == "env" else FORBIDDEN_IN_VALUE
    if any(ch in value for ch in forbidden):
        if mode == "env":
            raise ValueRejected("an env value cannot contain a newline or a null byte")
        raise ValueRejected("the value cannot contain a null byte")
    return value


# --- directory handling -------------------------------------------------------


def _open_parent(dest: Path) -> int:
    """Open the destination's parent directory, creating what's missing.

    Walks down from the root one component at a time with `O_NOFOLLOW`, so a
    directory swapped for a symlink mid-write is an error rather than a
    redirect.
    """
    parts = dest.parent.parts
    fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in parts[1:]:
            try:
                nxt = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
            except FileNotFoundError:
                os.mkdir(name, DIR_MODE, dir_fd=fd)
                nxt = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.fchmod(nxt, DIR_MODE)  # mkdir's mode is masked by the umask
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _atomic_write(dest: Path, content: str) -> None:
    parent_fd = _open_parent(dest)
    tmp_name = f".{dest.name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            FILE_MODE,
            dir_fd=parent_fd,
        )
        try:
            os.fchmod(fd, FILE_MODE)  # mode before content, whatever the umask did
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.unlink(tmp_name, dir_fd=parent_fd)
            raise
        os.replace(tmp_name, dest.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)  # the rename itself has to survive a power cut
    finally:
        os.close(parent_fd)


# --- dotenv rendering ---------------------------------------------------------


def _env_line(key: str, value: str, exported: bool) -> str:
    prefix = "export " if exported else ""
    return f"{prefix}{key}={shlex.quote(value)}"


def _render_env(existing: str, key: str, value: str) -> str:
    assignment = re.compile(rf"^\s*(export\s+)?{re.escape(key)}\s*=")
    lines = existing.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    out: list[str] = []
    replaced = False
    for line in lines:
        match = assignment.match(line)
        if not match:
            out.append(line)
            continue
        if replaced:
            continue  # drop stale duplicates so the file has one answer
        out.append(_env_line(key, value, exported=bool(match.group(1))))
        replaced = True

    if not replaced:
        out.append(_env_line(key, value, exported=False))

    return "\n".join(out) + "\n"


def _read_existing(dest: Path) -> str:
    """Read the dotenv file without following a symlink at the final component."""
    try:
        fd = os.open(dest, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ""
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        return handle.read()


def write_secret(dest: Path | str, mode: str, key: str | None, value: str) -> None:
    """Write `value` to `dest` in `mode`. Never logs or returns the value."""
    validate_mode(mode, key)
    dest = Path(dest)
    value = validate_value(value, mode)

    if mode == "file":
        _atomic_write(dest, value)
        return

    _atomic_write(dest, _render_env(_read_existing(dest), key, value))
