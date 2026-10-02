"""Configuration loading.

Config is a JSON file. Everything has a default except the allowlist, which is
deliberately empty so a fresh install can write nowhere until you say where.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("SECRETDROP_CONFIG", "~/.config/secretdrop/config.json")
).expanduser()

DEFAULT_PORT = 7391
DEFAULT_TTL_SECONDS = 1800
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 24 * 3600


class ConfigError(ValueError):
    """The config asks for something secretdrop will not do."""


def clamp_ttl(seconds: float) -> int:
    if seconds <= 0:
        raise ConfigError("ttl must be positive")
    return int(min(max(seconds, MIN_TTL_SECONDS), MAX_TTL_SECONDS))


def check_bind(address: str, allow_unspecified: bool) -> str:
    """Refuse 0.0.0.0 and :: unless the operator has explicitly asked for them."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address  # a hostname; let the socket layer have its say
    if parsed.is_unspecified and not allow_unspecified:
        raise ConfigError(
            f"refusing to bind {address}: that is every interface. "
            "Set allow_unspecified_bind to true if you really mean it."
        )
    return address


@dataclass
class Config:
    bind: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    base_url: str | None = None
    state_path: Path = Path("~/.local/state/secretdrop/pending.json")
    allowlist: list[str] = field(default_factory=list)
    default_ttl_seconds: int = DEFAULT_TTL_SECONDS
    callback_command: str | None = None
    callback_url: str | None = None
    callback_timeout_seconds: int = 20
    allow_unspecified_bind: bool = False
    _bound_port: int | None = None

    def note_bound_port(self, port: int) -> None:
        """Remember the real port when the config asked for 0 (tests do)."""
        self._bound_port = port

    @property
    def effective_port(self) -> int:
        return self._bound_port or self.port

    @property
    def allowed_hosts(self) -> set[str]:
        """Host headers this server will answer to."""
        from urllib.parse import urlparse

        hosts = set()
        host = f"[{self.bind}]" if ":" in self.bind else self.bind
        hosts.add(f"{host}:{self.effective_port}")
        hosts.add(host)
        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.netloc:
                hosts.add(parsed.netloc)
                if parsed.hostname:
                    hosts.add(parsed.hostname)
        return hosts

    @property
    def url_base(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        host = f"[{self.bind}]" if ":" in self.bind else self.bind
        return f"http://{host}:{self.effective_port}"

    def url_for(self, drop_id: str) -> str:
        return f"{self.url_base}/d/{drop_id}"


def tailscale_ip() -> str | None:
    """The host's IPv4 tailnet address, if Tailscale is up."""
    import subprocess

    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.strip().splitlines()
    return first[0].strip() if first else None


def load(path: Path | str | None = None) -> Config:
    path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    raw: dict = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))

    bind = raw.get("bind")
    if bind in (None, "tailscale"):
        bind = tailscale_ip() or "127.0.0.1"

    allow_unspecified = bool(raw.get("allow_unspecified_bind", False))
    return Config(
        bind=check_bind(bind, allow_unspecified),
        allow_unspecified_bind=allow_unspecified,
        port=int(raw.get("port", DEFAULT_PORT)),
        base_url=raw.get("base_url"),
        state_path=Path(raw.get("state_path", "~/.local/state/secretdrop/pending.json")).expanduser(),
        allowlist=list(raw.get("allowlist", [])),
        default_ttl_seconds=clamp_ttl(float(raw.get("default_ttl_seconds", DEFAULT_TTL_SECONDS))),
        callback_command=raw.get("callback_command"),
        callback_url=raw.get("callback_url"),
        callback_timeout_seconds=int(raw.get("callback_timeout_seconds", 20)),
    )
