"""Telling the agent the secret landed.

The callback carries metadata only. An agent that wants the value reads the
destination file itself, with its own permissions, on its own terms.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request

from .config import Config
from .store import DropRequest

log = logging.getLogger("secretdrop")


def callback_env(req: DropRequest) -> dict[str, str]:
    """The documented variables. The value is never one of them."""
    env = {
        "SECRETDROP_ID": req.id,
        "SECRETDROP_LABEL": req.label,
        "SECRETDROP_DEST": req.dest,
        "SECRETDROP_MODE": req.mode,
        "SECRETDROP_KEY": req.key or "",
    }
    # Only set when the link asked for a particular session, so a drop without
    # one inherits whatever the service environment already says.
    if req.session:
        env["SECRETDROP_SESSION"] = req.session
    return env


def _run_command(cfg: Config, req: DropRequest) -> None:
    import os

    env = dict(os.environ)
    env.update(callback_env(req))
    try:
        result = subprocess.run(
            cfg.callback_command,
            shell=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=cfg.callback_timeout_seconds,
        )
    except subprocess.SubprocessError as exc:
        log.warning("callback command failed for %s: %s", req.id[:8], exc)
        return
    if result.returncode != 0:
        log.warning("callback command exited %s for %s", result.returncode, req.id[:8])
        # A chatty callback could echo the secret, so its output stays at DEBUG.
        log.debug("callback stderr for %s: %s", req.id[:8], result.stderr.strip()[:500])


def _post_webhook(cfg: Config, req: DropRequest) -> None:
    payload = json.dumps(
        {
            "id": req.id,
            "label": req.label,
            "dest": req.dest,
            "mode": req.mode,
            "key": req.key,
        }
    ).encode()
    request = urllib.request.Request(
        cfg.callback_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg.callback_timeout_seconds):
            pass
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("callback webhook failed for %s: %s", req.id[:8], exc)


def fire(cfg: Config, req: DropRequest) -> None:
    """Run the configured callbacks.

    There is deliberately no parameter for the value, so no future caller can
    pass one. Failures are logged, never raised: the secret is already on disk
    and the submit must not look like it failed.
    """
    if cfg.callback_command:
        _run_command(cfg, req)
    if cfg.callback_url:
        _post_webhook(cfg, req)
