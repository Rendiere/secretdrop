"""The HTTP surface.

Two routes. Anything unknown, used, or expired gets the same page and the same
status, so the server never confirms which ids ever existed.

The handler reads or refuses a request body before it answers anything. An
unread body on a keep-alive connection gets parsed as the next request line and
echoed back in the error page, which with this server would mean echoing the
secret. Every error path either drains the body or closes the connection.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import pages
from .callback import fire
from .config import Config
from .paths import PathRejected, resolve_destination
from .store import Store
from .writer import ValueRejected, validate_value, write_secret

log = logging.getLogger("secretdrop")

DROP_PATH = re.compile(r"^/d/([A-Za-z0-9_-]{16,128})$")
CONTENT_LENGTH = re.compile(r"^[0-9]{1,7}$")
MAX_BODY_BYTES = 64 * 1024
MAX_CONCURRENT = 32


class Handler(BaseHTTPRequestHandler):
    server_version = "secretdrop"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    # A client that opens a socket and says nothing must not hold a thread.
    timeout = 15

    cfg: Config
    store: Store
    slots: threading.BoundedSemaphore

    # --- plumbing ---------------------------------------------------------

    def handle_one_request(self) -> None:
        if not self.slots.acquire(blocking=False):
            self.close_connection = True
            return
        try:
            super().handle_one_request()
        except TimeoutError:
            self.close_connection = True
        finally:
            self.slots.release()

    def log_message(self, fmt: str, *args) -> None:
        """Log the route and nothing else. A body never reaches the log."""
        log.info("%s %s", self.command or "-", self._route_label())

    def log_error(self, fmt: str, *args) -> None:
        """`BaseHTTPRequestHandler` would put the raw request line in here."""
        log.warning("malformed request on %s", self._route_label())

    def _route_label(self) -> str:
        try:
            path = urllib.parse.urlparse(self.path).path
        except (AttributeError, ValueError):
            return "-"
        match = DROP_PATH.match(path)
        return f"/d/{match.group(1)[:8]}..." if match else "/"

    def _respond(self, status: int, body: bytes, close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        if close:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _gone(self, close: bool = False) -> None:
        self._respond(404, pages.gone_page(), close=close)

    def _drop_id(self) -> str | None:
        match = DROP_PATH.match(urllib.parse.urlparse(self.path).path)
        return match.group(1) if match else None

    def _read_exactly(self, length: int) -> bytes:
        """`rfile.read` can come up short on a slow client; insist on the lot."""
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 8192))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _body_length(self) -> int | None:
        """The declared body size, or `None` if we will not parse this request."""
        if self.headers.get("Transfer-Encoding"):
            return None  # chunked bodies would leave undrained bytes behind
        raw = self.headers.get("Content-Length", "0").strip()
        if not CONTENT_LENGTH.fullmatch(raw):
            return None
        length = int(raw)
        return None if length > MAX_BODY_BYTES else length

    def _host_allowed(self) -> bool:
        """Blunt DNS-rebinding guard; the unguessable id is the real defence."""
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return False
        return host in self.cfg.allowed_hosts

    # --- routes -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._gone(close=True)
            return
        drop_id = self._drop_id()
        req = self.store.get(drop_id) if drop_id else None
        if req is None:
            self._gone()
            return
        self._respond(200, pages.drop_page(req))

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._gone(close=True)
            return

        # Settle the body first. Anything we will not read, we refuse and hang up,
        # so leftover bytes can never be parsed as the next request.
        length = self._body_length()
        if length is None:
            self._gone(close=True)
            return

        drop_id = self._drop_id()
        if drop_id is None:
            self._read_exactly(length)
            self._gone(close=True)
            return

        raw = self._read_exactly(length).decode("utf-8", "replace")
        fields = urllib.parse.parse_qs(raw, keep_blank_values=True)
        value = (fields.get("value") or [""])[0]
        del raw, fields

        peek = self.store.get(drop_id)
        if peek is None:
            self._gone(close=True)
            return

        try:
            value = validate_value(value, peek.mode)
        except ValueRejected as exc:
            # A bad paste is the human's mistake, so the link stays alive.
            self._respond(400, pages.drop_page(peek, error=str(exc)))
            return

        try:
            dest = resolve_destination(peek.dest, self.cfg.allowlist)
        except PathRejected as exc:
            log.error("refusing drop %s: %s", drop_id[:8], exc)
            self.store.revoke(drop_id)
            self._gone(close=True)
            return

        # Take the request first: whoever wins the consume owns the single use.
        req = self.store.consume(drop_id)
        if req is None:
            self._gone(close=True)
            return

        try:
            write_secret(dest, req.mode, req.key, value)
        except (OSError, ValueError) as exc:
            log.error("write failed for %s: %s", drop_id[:8], type(exc).__name__)
            self.store.record_outcome(req, "failed")
            self._respond(500, pages.failed_page(), close=True)
            return
        finally:
            value = ""

        self.store.record_outcome(req, "landed")
        log.info("wrote %s to %s", req.label, req.dest)
        threading.Thread(target=fire, args=(self.cfg, req), daemon=True).start()
        self._respond(200, pages.done_page(req))


def build_server(cfg: Config, store: Store | None = None) -> ThreadingHTTPServer:
    store = store or Store(cfg.state_path)
    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "cfg": cfg,
            "store": store,
            "slots": threading.BoundedSemaphore(MAX_CONCURRENT),
        },
    )
    httpd = ThreadingHTTPServer((cfg.bind, cfg.port), handler)
    httpd.daemon_threads = True
    cfg.note_bound_port(httpd.server_address[1])
    return httpd


def serve(cfg: Config) -> None:
    try:
        httpd = build_server(cfg)
    except OSError as exc:
        raise SystemExit(
            f"secretdrop: cannot bind {cfg.bind}:{cfg.port}: {exc.strerror}"
        ) from exc
    log.info("secretdrop listening on %s", cfg.url_base)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
