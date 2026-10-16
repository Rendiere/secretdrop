"""Command line: make a link, serve them, list them, kill them."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import sys
import time

from . import __version__
from .config import BIND_WAIT_SECONDS, Config, ConfigError, load
from .config import clamp_ttl
from .paths import PathRejected, check_text, resolve_destination
from .server import serve
from .store import Store
from .writer import validate_mode

DURATION = re.compile(r"^(\d+)\s*([smhd])$")
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
# The key reaches a shell callback, so only the characters OpenClaw session keys use.
SESSION = re.compile(r"^[A-Za-z0-9:._@+-]{1,200}$")
WAIT_GRACE_SECONDS = 10
FULL_ID_LENGTH = 32


def parse_duration(text: str) -> int:
    """`30m`, `2h`, `45s`, `1d`, or a bare number of seconds."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    match = DURATION.match(text)
    if not match:
        raise argparse.ArgumentTypeError(f"cannot read '{text}' as a duration (try 30m, 2h, 1d)")
    return int(match.group(1)) * DURATION_UNITS[match.group(2)]


def cmd_link(args, cfg: Config) -> int:
    try:
        validate_mode(args.mode, args.key)
    except ValueError as exc:
        print(f"secretdrop: {exc}", file=sys.stderr)
        return 2

    try:
        check_text(args.label, "label")
        if args.session:
            check_text(args.session, "session")
        dest = resolve_destination(args.dest, cfg.allowlist)
    except PathRejected as exc:
        print(f"secretdrop: {exc}", file=sys.stderr)
        return 2

    if args.session is not None and not SESSION.match(args.session):
        print(
            "secretdrop: session must be 1-200 characters of letters, digits and :._@+-",
            file=sys.stderr,
        )
        return 2

    try:
        ttl = clamp_ttl(args.ttl if args.ttl is not None else cfg.default_ttl_seconds)
    except ConfigError as exc:
        print(f"secretdrop: {exc}", file=sys.stderr)
        return 2
    req = Store(cfg.state_path).create(
        dest=str(dest),
        label=args.label,
        mode=args.mode,
        key=args.key,
        ttl_seconds=ttl,
        session=args.session,
    )
    url = cfg.url_for(req.id)

    if args.json:
        print(
            json.dumps(
                {
                    "id": req.id,
                    "url": url,
                    "dest": str(dest),
                    "created_at": req.created_at,
                    "expires_at": req.expires_at,
                    "session": req.session,
                }
            )
        )
    else:
        print(url)
    return 0


def cmd_serve(args, cfg: Config) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("secretdrop")
    if not cfg.allowlist:
        log.warning("allowlist is empty — every drop will be refused")
    if cfg.bind_mode == "tailscale" and not cfg.bind:
        log.info("waiting up to %ss for a tailnet address", BIND_WAIT_SECONDS)
    try:
        cfg.resolve_bind(BIND_WAIT_SECONDS)
    except ConfigError as exc:
        # Exit non-zero so systemd restarts us rather than leaving a server
        # running on an address no one can reach.
        log.error("%s", exc)
        return 2
    serve(cfg)
    return 0


def cmd_list(args, cfg: Config) -> int:
    pending = Store(cfg.state_path).list_pending()
    if args.json:
        print(
            json.dumps(
                [
                    {"id": r.id, "label": r.label, "dest": r.dest, "mode": r.mode,
                     "key": r.key, "session": r.session,
                     "expires_at": r.expires_at, "url": cfg.url_for(r.id)}
                    for r in pending
                ],
                indent=2,
            )
        )
        return 0
    if not pending:
        print("no pending drops")
        return 0
    for r in pending:
        expires = _dt.datetime.fromtimestamp(r.expires_at).strftime("%H:%M")
        print(f"{r.id[:8]}…  {r.label}  -> {r.dest}  (expires {expires})")
    return 0


def cmd_revoke(args, cfg: Config) -> int:
    store = Store(cfg.state_path)
    matches = [r.id for r in store.list_pending() if r.id.startswith(args.id)]
    if len(matches) > 1:
        print(f"secretdrop: '{args.id}' matches {len(matches)} drops", file=sys.stderr)
        return 2
    target = matches[0] if matches else args.id
    if store.revoke(target):
        print(f"revoked {target[:8]}…")
        return 0
    print(f"secretdrop: no drop matching '{args.id}'", file=sys.stderr)
    return 1


def _wait_result(args, drop_id: str, outcome: str, label: str, dest: str) -> int:
    if args.json:
        print(json.dumps({"id": drop_id, "outcome": outcome, "label": label, "dest": dest}))
    else:
        print(f"{outcome}: {label} -> {dest}")
    if outcome == "landed":
        return 0
    return 3 if outcome == "timeout" else 1


def cmd_wait(args, cfg: Config) -> int:
    store = Store(cfg.state_path)
    matches = [i for i in store.known_ids() if i.startswith(args.id)]
    if len(matches) > 1:
        print(f"secretdrop: '{args.id}' matches {len(matches)} drops", file=sys.stderr)
        return 2
    if matches:
        drop_id = matches[0]
    elif len(args.id) == FULL_ID_LENGTH:
        drop_id = args.id
    else:
        print(f"secretdrop: no drop matching '{args.id}'", file=sys.stderr)
        return 1

    deadline = time.time() + args.timeout if args.timeout else None
    label = dest = ""
    gone_since = None
    while True:
        record = store.outcome(drop_id)
        if record:
            return _wait_result(args, drop_id, record["outcome"], record["label"], record["dest"])

        req = store.peek(drop_id)
        if req is not None:
            label, dest = req.label, req.dest
            if req.expired:
                return _wait_result(args, drop_id, "expired", label, dest)
        else:
            # Consumed but not yet recorded: the write is in flight.
            gone_since = gone_since or time.time()
            if time.time() - gone_since > WAIT_GRACE_SECONDS:
                return _wait_result(args, drop_id, "unknown", label, dest)

        if deadline is not None and time.time() >= deadline:
            return _wait_result(args, drop_id, "timeout", label, dest)
        time.sleep(args.poll)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="secretdrop", description="Hand a secret to a machine over your tailnet, once."
    )
    parser.add_argument("--version", action="version", version=f"secretdrop {__version__}")
    parser.add_argument("--config", help="path to config.json")
    subs = parser.add_subparsers(dest="command", required=True)

    link = subs.add_parser("link", help="create a one-time drop link")
    link.add_argument("--dest", required=True, help="file the secret is written to")
    link.add_argument("--label", required=True, help="what the human will see, e.g. 'Telegram bot token'")
    link.add_argument("--mode", default="file", choices=["file", "env"])
    link.add_argument("--key", help="variable name, required for --mode env")
    link.add_argument(
        "--ttl",
        type=parse_duration,
        help="link lifetime, e.g. 30m. Clamped to 1 min–24 h (default from config)",
    )
    link.add_argument(
        "--session",
        help="OpenClaw session key to wake, e.g. agent:etch:telegram:group:-100123 "
        "(default: whatever the callback is configured with)",
    )
    link.add_argument("--json", action="store_true")
    link.set_defaults(func=cmd_link)

    srv = subs.add_parser("serve", help="run the HTTP server")
    srv.add_argument("--verbose", action="store_true")
    srv.set_defaults(func=cmd_serve)

    listing = subs.add_parser("list", help="show pending drops")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    revoke = subs.add_parser("revoke", help="kill a pending drop")
    revoke.add_argument("id", help="full id or unique prefix")
    revoke.set_defaults(func=cmd_revoke)

    wait = subs.add_parser("wait", help="block until a drop lands, fails, is revoked or expires")
    wait.add_argument("id", help="full id or unique prefix")
    wait.add_argument("--timeout", type=parse_duration, help="give up after this long, e.g. 90s")
    wait.add_argument("--json", action="store_true")
    wait.add_argument("--poll", type=float, default=1.0, help=argparse.SUPPRESS)
    wait.set_defaults(func=cmd_wait)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load(args.config)
        # `serve` sits through a boot waiting for the tailnet; the other commands
        # answer in a moment or not at all, so they take one look and give up.
        # `serve` does its own waiting, once the log is set up to narrate it.
        if args.command != "serve":
            cfg.resolve_bind(0.0)
    except ConfigError as exc:
        print(f"secretdrop: {exc}", file=sys.stderr)
        return 2
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
