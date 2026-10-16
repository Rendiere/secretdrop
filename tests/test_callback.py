import os
import json
import tempfile
import unittest
from pathlib import Path

from secretdrop.callback import callback_env, fire
from secretdrop.config import Config
from secretdrop.store import DropRequest


def make_request(**kw):
    args = dict(
        id="abc123",
        dest="/tmp/bot.token",
        label="Telegram bot token",
        mode="env",
        key="BOT_TOKEN",
        created_at=0.0,
        expires_at=9e9,
    )
    args.update(kw)
    return DropRequest(**args)


class CallbackEnvTests(unittest.TestCase):
    def test_env_carries_the_metadata(self):
        env = callback_env(make_request())
        self.assertEqual(env["SECRETDROP_ID"], "abc123")
        self.assertEqual(env["SECRETDROP_LABEL"], "Telegram bot token")
        self.assertEqual(env["SECRETDROP_DEST"], "/tmp/bot.token")
        self.assertEqual(env["SECRETDROP_MODE"], "env")
        self.assertEqual(env["SECRETDROP_KEY"], "BOT_TOKEN")

    def test_env_has_no_value_key(self):
        env = callback_env(make_request())
        self.assertNotIn("SECRETDROP_VALUE", env)
        self.assertNotIn("SECRETDROP_SECRET", env)

    def test_a_drop_without_a_session_sets_no_session_variable(self):
        """Silence here is what lets the service environment's default stand."""
        self.assertNotIn("SECRETDROP_SESSION", callback_env(make_request()))

    def test_a_drop_with_a_session_carries_it(self):
        env = callback_env(make_request(session="agent:etch:telegram:group:-100123:topic:2577"))
        self.assertEqual(
            env["SECRETDROP_SESSION"], "agent:etch:telegram:group:-100123:topic:2577"
        )

    def test_the_session_overrides_the_ambient_one(self):
        import os

        cfg = Config(callback_command="printf '%s' \"$SECRETDROP_SESSION\" > " + self.seen)
        os.environ["SECRETDROP_SESSION"] = "agent:main:telegram:group:-100999"
        try:
            fire(cfg, make_request(session="agent:etch:telegram:group:-100123"))
        finally:
            del os.environ["SECRETDROP_SESSION"]
        self.assertEqual(
            Path(self.seen).read_text(), "agent:etch:telegram:group:-100123"
        )

    def test_without_one_the_ambient_session_survives(self):
        import os

        cfg = Config(callback_command="printf '%s' \"$SECRETDROP_SESSION\" > " + self.seen)
        os.environ["SECRETDROP_SESSION"] = "agent:main:telegram:group:-100999"
        try:
            fire(cfg, make_request())
        finally:
            del os.environ["SECRETDROP_SESSION"]
        self.assertEqual(
            Path(self.seen).read_text(), "agent:main:telegram:group:-100999"
        )

    def setUp(self):
        self.seen = str(Path(tempfile.mkdtemp()) / "session")

    def test_env_is_only_the_five_documented_variables(self):
        env = callback_env(make_request())
        self.assertEqual(
            sorted(env),
            [
                "SECRETDROP_DEST",
                "SECRETDROP_ID",
                "SECRETDROP_KEY",
                "SECRETDROP_LABEL",
                "SECRETDROP_MODE",
            ],
        )

    def test_missing_key_becomes_an_empty_string(self):
        env = callback_env(make_request(mode="file", key=None))
        self.assertEqual(env["SECRETDROP_KEY"], "")


class FireCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))

    def test_fire_has_no_parameter_for_the_value(self):
        import inspect

        self.assertEqual(list(inspect.signature(fire).parameters), ["cfg", "req"])

    def test_command_runs_and_sees_only_the_metadata(self):
        out = self.tmp / "out.json"
        cfg = Config(
            callback_command=(
                'python3 -c "import json,os,sys;'
                "json.dump({k:v for k,v in os.environ.items() if k.startswith('SECRETDROP')},"
                f"open('{out}','w'))\""
            )
        )
        fire(cfg, make_request())
        captured = json.loads(out.read_text())
        self.assertEqual(captured["SECRETDROP_ID"], "abc123")
        self.assertNotIn("super-secret-value", json.dumps(captured))

    def test_a_failing_command_does_not_raise(self):
        cfg = Config(callback_command="exit 3")
        fire(cfg, make_request())  # must not raise

    def test_no_callback_configured_is_a_no_op(self):
        fire(Config(), make_request())


if __name__ == "__main__":
    unittest.main()


class SessionCallbackTests(unittest.TestCase):
    def test_a_session_is_exported_when_the_link_has_one(self):
        env = callback_env(make_request(session="agent:main:telegram:group:-100123"))
        self.assertEqual(env["SECRETDROP_SESSION"], "agent:main:telegram:group:-100123")

    def test_no_session_leaves_the_variable_unset(self):
        self.assertNotIn("SECRETDROP_SESSION", callback_env(make_request()))

    def test_a_service_level_session_reaches_the_command_when_the_link_has_none(self):
        tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        out = tmp / "out.txt"
        cfg = Config(callback_command=f'printf %s "$SECRETDROP_SESSION" > {out}')
        old = os.environ.get("SECRETDROP_SESSION")
        os.environ["SECRETDROP_SESSION"] = "agent:main:main"
        try:
            fire(cfg, make_request())
        finally:
            if old is None:
                del os.environ["SECRETDROP_SESSION"]
            else:
                os.environ["SECRETDROP_SESSION"] = old
        self.assertEqual(out.read_text(), "agent:main:main")

    def test_the_webhook_payload_carries_the_session(self):
        import http.server
        import threading

        received = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                received.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=httpd.handle_request, daemon=True)
        t.start()
        fire(Config(callback_url=f"http://127.0.0.1:{httpd.server_address[1]}/"), make_request(session="s:1"))
        t.join(timeout=5)
        httpd.server_close()
        self.assertEqual(received["session"], "s:1")
        self.assertNotIn("value", received)
