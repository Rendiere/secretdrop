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
        self.tmp = Path(tempfile.mkdtemp())

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
