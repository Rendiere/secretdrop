import os
import logging
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from secretdrop.config import Config
from secretdrop.server import build_server
from secretdrop.store import Store

SECRET = "sk-super-secret-do-not-leak-123"


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.creds = self.tmp / "creds"
        self.creds.mkdir()
        self.callback_out = self.tmp / "callback.json"
        self.cfg = Config(
            bind="127.0.0.1",
            port=0,
            allowlist=[str(self.creds / "*")],
            callback_command=(
                'python3 -c "import json,os;'
                "json.dump({k:v for k,v in os.environ.items() if k.startswith('SECRETDROP')},"
                f"open('{self.callback_out}','w'))\""
            ),
        )
        self.store = Store(self.tmp / "state" / "pending.json")
        self.httpd = build_server(self.cfg, self.store)
        self.port = self.httpd.server_address[1]
        self.logs = LogCapture()
        logging.getLogger("secretdrop").addHandler(self.logs)
        logging.getLogger("secretdrop").setLevel(logging.DEBUG)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        logging.getLogger("secretdrop").removeHandler(self.logs)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def url(self, drop_id):
        return f"http://127.0.0.1:{self.port}/d/{drop_id}"

    def make(self, **kw):
        args = dict(
            dest=str(self.creds / "bot.token"),
            label="Telegram bot token",
            mode="file",
            key=None,
            ttl_seconds=600,
        )
        args.update(kw)
        return self.store.create(**args)

    def get(self, drop_id):
        try:
            with urllib.request.urlopen(self.url(drop_id)) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def post(self, drop_id, value=SECRET):
        data = urllib.parse.urlencode({"value": value}).encode()
        try:
            with urllib.request.urlopen(self.url(drop_id), data=data) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage() % () if not record.args else record.getMessage())

    @property
    def text(self):
        return "\n".join(self.lines)


class DropPageTests(ServerTestCase):
    def test_get_shows_the_label_and_destination(self):
        req = self.make()
        status, body = self.get(req.id)
        self.assertEqual(status, 200)
        self.assertIn("Telegram bot token", body)
        self.assertIn(str(self.creds / "bot.token"), body)

    def test_get_shows_the_key_in_env_mode(self):
        req = self.make(
            dest=str(self.creds / ".secrets.env"), mode="env", key="BOT_TOKEN"
        )
        _, body = self.get(req.id)
        self.assertIn("BOT_TOKEN", body)

    def test_unknown_id_is_a_generic_404(self):
        status, body = self.get("a" * 32)
        self.assertEqual(status, 404)
        self.assertIn("Nothing here", body)

    def test_expired_id_looks_exactly_like_an_unknown_one(self):
        req = self.make(ttl_seconds=-1)
        expired = self.get(req.id)
        unknown = self.get("b" * 32)
        self.assertEqual(expired, unknown)

    def test_short_or_malformed_path_is_404(self):
        for path in ["/", "/d/", "/d/short", "/d/../../etc/passwd", "/etc/passwd"]:
            with self.subTest(path=path):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
                        self.assertEqual(r.status, 404)
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 404)


class SubmitTests(ServerTestCase):
    def test_post_writes_the_secret(self):
        req = self.make()
        status, body = self.post(req.id)
        self.assertEqual(status, 200)
        self.assertIn("Saved", body)
        self.assertEqual((self.creds / "bot.token").read_text(), SECRET)

    def test_destination_is_0600(self):
        req = self.make()
        self.post(req.id)
        self.assertEqual((self.creds / "bot.token").stat().st_mode & 0o777, 0o600)

    def test_env_mode_replaces_the_key(self):
        dest = self.creds / ".secrets.env"
        dest.write_text("OTHER=1\nBOT_TOKEN=old\n")
        req = self.make(dest=str(dest), mode="env", key="BOT_TOKEN")
        self.post(req.id)
        self.assertEqual(dest.read_text(), f"OTHER=1\nBOT_TOKEN={SECRET}\n")

    def test_link_is_single_use(self):
        req = self.make()
        self.assertEqual(self.post(req.id)[0], 200)
        status, body = self.post(req.id, "second-attempt")
        self.assertEqual(status, 404)
        self.assertIn("Nothing here", body)
        self.assertEqual((self.creds / "bot.token").read_text(), SECRET)

    def test_expired_link_cannot_be_submitted(self):
        req = self.make(ttl_seconds=-1)
        self.assertEqual(self.post(req.id)[0], 404)
        self.assertFalse((self.creds / "bot.token").exists())

    def test_destination_outside_the_allowlist_is_refused(self):
        outside = self.tmp / "outside.token"
        req = self.make(dest=str(outside))
        self.assertEqual(self.post(req.id)[0], 404)
        self.assertFalse(outside.exists())

    def test_traversal_destination_is_refused(self):
        req = self.make(dest=str(self.creds / ".." / "escape.token"))
        self.assertEqual(self.post(req.id)[0], 404)
        self.assertFalse((self.tmp / "escape.token").exists())

    def test_a_symlink_planted_after_the_link_was_made_is_caught(self):
        # The allowlist is re-checked when the link is used, not just when made.
        import os

        outside = self.tmp / "outside"
        outside.mkdir()
        nest = self.creds / "nest"
        nest.mkdir()
        req = self.make(dest=str(nest / "bot.token"))
        nest.rmdir()
        os.symlink(outside, nest)
        self.assertEqual(self.post(req.id)[0], 404)
        self.assertFalse((outside / "bot.token").exists())

    def test_a_write_failure_says_so_instead_of_pretending_the_link_was_bad(self):
        import os

        req = self.make()
        os.chmod(self.creds, 0o500)  # readable, not writable
        try:
            status, body = self.post(req.id)
            self.assertEqual(status, 500)
            self.assertIn("Could not save", body)
            self.assertEqual(list(self.creds.iterdir()), [])  # no temp file left
        finally:
            os.chmod(self.creds, 0o700)

    def test_a_refused_destination_also_kills_the_link(self):
        req = self.make(dest=str(self.tmp / "outside.token"))
        self.post(req.id)
        self.assertIsNone(self.store.get(req.id))

    def test_empty_value_is_rejected_and_the_link_survives(self):
        req = self.make()
        status, body = self.post(req.id, "   ")
        self.assertEqual(status, 400)
        self.assertIn("value is empty", body)
        self.assertIsNotNone(self.store.get(req.id))


class SecrecyTests(ServerTestCase):
    def test_the_value_is_not_in_the_response(self):
        req = self.make()
        _, body = self.post(req.id)
        self.assertNotIn(SECRET, body)

    def test_the_value_is_not_in_the_logs(self):
        req = self.make()
        self.post(req.id)
        self.assertNotIn(SECRET, self.logs.text)

    def test_the_value_is_not_in_the_state_file(self):
        req = self.make()
        self.post(req.id)
        self.assertNotIn(SECRET, self.store.path.read_text())

    def test_the_callback_env_has_the_metadata_and_not_the_value(self):
        import json
        import time

        req = self.make()
        self.post(req.id)
        for _ in range(100):
            if self.callback_out.exists():
                break
            time.sleep(0.05)
        captured = json.loads(self.callback_out.read_text())
        self.assertEqual(captured["SECRETDROP_LABEL"], "Telegram bot token")
        self.assertEqual(captured["SECRETDROP_DEST"], str(self.creds / "bot.token"))
        self.assertEqual(captured["SECRETDROP_MODE"], "file")
        self.assertNotIn(SECRET, json.dumps(captured))

    def test_a_newline_bearing_env_value_is_refused_and_writes_nothing(self):
        dest = self.creds / ".secrets.env"
        dest.write_text("OTHER=1\n")
        req = self.make(dest=str(dest), mode="env", key="BOT_TOKEN")
        status, body = self.post(req.id, "value\nEVIL=1")
        self.assertEqual(status, 400)
        self.assertIn("newline", body)
        self.assertEqual(dest.read_text(), "OTHER=1\n")
        self.assertIsNotNone(self.store.get(req.id))  # the human can try again

    def test_an_oversized_body_is_rejected_without_writing(self):
        req = self.make()
        status, _ = self.post(req.id, "x" * (64 * 1024 + 10))
        self.assertEqual(status, 404)
        self.assertFalse((self.creds / "bot.token").exists())


if __name__ == "__main__":
    unittest.main()


class OutcomeTests(ServerTestCase):
    def test_a_successful_drop_records_landed(self):
        req = self.make()
        self.post(req.id)
        self.assertEqual(self.store.outcome(req.id)["outcome"], "landed")

    def test_a_failed_write_records_failed(self):
        req = self.make()
        os.chmod(self.creds, 0o500)
        try:
            self.post(req.id)
        finally:
            os.chmod(self.creds, 0o700)
        self.assertEqual(self.store.outcome(req.id)["outcome"], "failed")

    def test_the_outcome_record_never_holds_the_value(self):
        req = self.make()
        self.post(req.id)
        outcomes = self.store.outcomes_path.read_text()
        self.assertNotIn(SECRET, outcomes)

    def test_the_callback_gets_the_links_session(self):
        import json
        import time

        req = self.make(session="agent:main:telegram:group:-100123")
        self.post(req.id)
        for _ in range(100):
            if self.callback_out.exists():
                break
            time.sleep(0.05)
        captured = json.loads(self.callback_out.read_text())
        self.assertEqual(captured["SECRETDROP_SESSION"], "agent:main:telegram:group:-100123")
