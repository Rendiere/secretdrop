"""The leak the review found: an unread body re-parsed as the next request.

`BaseHTTPRequestHandler` puts a malformed request line into its 400 page. On a
keep-alive connection, a POST body the server never read becomes that line — so
a refused drop could echo the secret straight back to the client.
"""

import socket
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from secretdrop.config import Config
from secretdrop.server import build_server
from secretdrop.store import Store

SECRET = "sk-raw-socket-canary-9f3a"


class RawSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.creds = self.tmp / "creds"
        self.creds.mkdir()
        self.cfg = Config(bind="127.0.0.1", port=0, allowlist=[str(self.creds / "*")])
        self.store = Store(self.tmp / "state" / "pending.json")
        self.httpd = build_server(self.cfg, self.store)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def raw_exchange(self, requests: bytes) -> bytes:
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.sendall(requests)
        chunks = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except (TimeoutError, OSError):
            pass
        finally:
            sock.close()
        return b"".join(chunks)

    def post_bytes(self, drop_id: str, value: str = SECRET) -> bytes:
        body = urllib.parse.urlencode({"value": value}).encode()
        return (
            f"POST /d/{drop_id} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode() + body

    def assert_clean(self, raw: bytes):
        self.assertNotIn(SECRET.encode(), raw, "the secret came back over the wire")

    def test_unknown_id_never_echoes_the_body(self):
        self.assert_clean(self.raw_exchange(self.post_bytes("a" * 32)))

    def test_used_id_never_echoes_the_body(self):
        req = self.store.create(
            dest=str(self.creds / "a.token"), label="T", mode="file", key=None, ttl_seconds=600
        )
        self.raw_exchange(self.post_bytes(req.id, "first-value"))
        self.assert_clean(self.raw_exchange(self.post_bytes(req.id)))

    def test_expired_id_never_echoes_the_body(self):
        req = self.store.create(
            dest=str(self.creds / "a.token"), label="T", mode="file", key=None, ttl_seconds=-1
        )
        self.assert_clean(self.raw_exchange(self.post_bytes(req.id)))

    def test_malformed_path_never_echoes_the_body(self):
        self.assert_clean(self.raw_exchange(self.post_bytes("short")))

    def test_pipelined_request_after_a_refusal_never_echoes_the_body(self):
        pipelined = self.post_bytes("a" * 32) + (
            f"GET /d/{'b' * 32} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n"
        ).encode()
        self.assert_clean(self.raw_exchange(pipelined))

    def test_chunked_body_is_refused_without_echoing(self):
        raw = self.raw_exchange(
            (
                f"POST /d/{'a' * 32} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.port}\r\n"
                "Transfer-Encoding: chunked\r\n\r\n"
                f"{len(SECRET):x}\r\n{SECRET}\r\n0\r\n\r\n"
            ).encode()
        )
        self.assert_clean(raw)

    def test_a_padded_content_length_is_refused(self):
        body = urllib.parse.urlencode({"value": SECRET}).encode()
        raw = self.raw_exchange(
            (
                f"POST /d/{'a' * 32} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.port}\r\n"
                f"Content-Length: +{len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        self.assert_clean(raw)

    def test_a_foreign_host_header_is_refused(self):
        req = self.store.create(
            dest=str(self.creds / "a.token"), label="T", mode="file", key=None, ttl_seconds=600
        )
        raw = self.raw_exchange(
            f"GET /d/{req.id} HTTP/1.1\r\nHost: evil.example.com\r\n\r\n".encode()
        )
        self.assertIn(b"404", raw.split(b"\r\n")[0])
        self.assertFalse((self.creds / "a.token").exists())

    def test_the_response_carries_the_hardening_headers(self):
        req = self.store.create(
            dest=str(self.creds / "a.token"), label="T", mode="file", key=None, ttl_seconds=600
        )
        raw = self.raw_exchange(
            f"GET /d/{req.id} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        for header in [b"Cache-Control: no-store", b"X-Frame-Options: DENY",
                       b"frame-ancestors 'none'", b"Referrer-Policy: no-referrer"]:
            self.assertIn(header, raw)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "state" / "pending.json")

    def test_only_one_of_many_racing_consumers_wins(self):
        req = self.store.create(
            dest="/tmp/x", label="T", mode="file", key=None, ttl_seconds=600
        )
        winners = []
        barrier = threading.Barrier(8)

        def attempt():
            barrier.wait()
            if self.store.consume(req.id) is not None:
                winners.append(1)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(winners), 1)


if __name__ == "__main__":
    unittest.main()
