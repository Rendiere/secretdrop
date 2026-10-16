import os
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from secretdrop.cli import main, parse_duration
from secretdrop.store import Store


class DurationTests(unittest.TestCase):
    def test_reads_the_documented_forms(self):
        self.assertEqual(parse_duration("45s"), 45)
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("90"), 90)

    def test_rejects_nonsense(self):
        for bad in ["", "soon", "30x", "-5m"]:
            with self.subTest(bad=bad):
                with self.assertRaises(Exception):
                    parse_duration(bad)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.creds = self.tmp / "creds"
        self.creds.mkdir()
        self.state = self.tmp / "state" / "pending.json"
        self.config = self.tmp / "config.json"
        self.config.write_text(
            json.dumps(
                {
                    "bind": "100.64.0.1",
                    "port": 7391,
                    "state_path": str(self.state),
                    "allowlist": [str(self.creds / "*")],
                    "default_ttl_seconds": 600,
                }
            )
        )

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--config", str(self.config), *argv])
        return code, out.getvalue().strip(), err.getvalue().strip()

    def test_link_prints_a_tailscale_url(self):
        code, out, _ = self.run_cli(
            "link", "--dest", str(self.creds / "bot.token"), "--label", "Telegram bot token"
        )
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("http://100.64.0.1:7391/d/"), out)
        self.assertEqual(len(Store(self.state).list_pending()), 1)

    def test_link_records_a_session_when_given_one(self):
        code, _, _ = self.run_cli(
            "link", "--dest", str(self.creds / "bot.token"), "--label", "Icon bot token",
            "--session", "agent:etch:telegram:group:-100123:topic:2577",
        )
        self.assertEqual(code, 0)
        req = Store(self.state).list_pending()[0]
        self.assertEqual(req.session, "agent:etch:telegram:group:-100123:topic:2577")

    def test_link_without_a_session_leaves_it_unset(self):
        self.run_cli("link", "--dest", str(self.creds / "bot.token"), "--label", "Icon bot token")
        self.assertIsNone(Store(self.state).list_pending()[0].session)

    def test_a_session_with_a_control_character_is_refused(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.creds / "bot.token"), "--label", "Icon bot token",
            "--session", "agent:main\nmalicious",
        )
        self.assertEqual(code, 2)
        self.assertIn("session", err)

    def test_link_json_carries_the_id_and_url(self):
        code, out, _ = self.run_cli(
            "link", "--dest", str(self.creds / "bot.token"), "--label", "T", "--json"
        )
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn(payload["id"], payload["url"])

    def test_link_refuses_a_destination_outside_the_allowlist(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.tmp / "outside.token"), "--label", "T"
        )
        self.assertEqual(code, 2)
        self.assertIn("allowlist", err)
        self.assertEqual(Store(self.state).list_pending(), [])

    def test_link_refuses_traversal(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.creds / ".." / "escape.token"), "--label", "T"
        )
        self.assertEqual(code, 2)
        self.assertIn("..", err)

    def test_env_mode_requires_a_key(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.creds / ".secrets.env"), "--label", "T", "--mode", "env"
        )
        self.assertEqual(code, 2)
        self.assertIn("key", err)

    def test_ttl_is_honoured(self):
        self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "T", "--ttl", "5m"
        )
        req = Store(self.state).list_pending()[0]
        self.assertAlmostEqual(req.expires_at - req.created_at, 300, delta=1)

    def test_list_and_revoke(self):
        _, url, _ = self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "Alpha"
        )
        drop_id = url.rsplit("/", 1)[-1]
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("Alpha", out)

        code, out, _ = self.run_cli("revoke", drop_id[:8])
        self.assertEqual(code, 0)
        self.assertEqual(Store(self.state).list_pending(), [])

        code, _, err = self.run_cli("revoke", drop_id[:8])
        self.assertEqual(code, 1)

    def test_list_is_empty_to_start(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(out, "no pending drops")


if __name__ == "__main__":
    unittest.main()


class ValidationTests(CliTests):
    def test_a_label_with_a_control_character_is_refused(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "Token\nFAKE LOG LINE"
        )
        self.assertEqual(code, 2)
        self.assertIn("control", err)

    def test_a_label_with_a_direction_override_is_refused(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "Token‮gnp.esle"
        )
        self.assertEqual(code, 2)
        self.assertIn("direction", err)

    def test_an_overlong_label_is_refused(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "x" * 201
        )
        self.assertEqual(code, 2)
        self.assertIn("longer than", err)

    def test_a_short_ttl_is_clamped_up_to_a_minute(self):
        self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "T", "--ttl", "1s"
        )
        req = Store(self.state).list_pending()[0]
        self.assertAlmostEqual(req.expires_at - req.created_at, 60, delta=1)

    def test_a_huge_ttl_is_clamped_down_to_a_day(self):
        self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "T", "--ttl", "90d"
        )
        req = Store(self.state).list_pending()[0]
        self.assertAlmostEqual(req.expires_at - req.created_at, 86400, delta=1)

    def test_a_zero_ttl_is_refused(self):
        code, _, err = self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "T", "--ttl", "0"
        )
        self.assertEqual(code, 2)
        self.assertIn("positive", err)


class SessionCliTests(CliTests):
    def test_link_stores_a_session_and_json_shows_it(self):
        code, out, _ = self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "T",
            "--session", "agent:main:telegram:group:-100123", "--json",
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["session"], "agent:main:telegram:group:-100123")
        self.assertIn("created_at", payload)
        _, listed, _ = self.run_cli("list", "--json")
        self.assertEqual(json.loads(listed)[0]["session"], "agent:main:telegram:group:-100123")

    def test_link_refuses_an_unsafe_session(self):
        for bad in ["has space", "quote'", "$(touch x)", "a\nb", "x" * 201, ""]:
            with self.subTest(bad=bad):
                code, _, err = self.run_cli(
                    "link", "--dest", str(self.creds / "a.token"), "--label", "T", "--session", bad
                )
                self.assertEqual(code, 2)
                self.assertIn("session", err)
        self.assertEqual(Store(self.state).list_pending(), [])


class WaitTests(CliTests):
    def link(self, ttl="10m"):
        _, out, _ = self.run_cli(
            "link", "--dest", str(self.creds / "a.token"), "--label", "Alpha", "--ttl", ttl, "--json"
        )
        return json.loads(out)["id"]

    def wait(self, *extra):
        code, out, err = self.run_cli("wait", *extra, "--json", "--poll", "0.05")
        return code, (json.loads(out) if out else None), err

    def test_landed(self):
        drop_id = self.link()
        store = Store(self.state)
        req = store.consume(drop_id)
        store.record_outcome(req, "landed")
        code, out, _ = self.wait(drop_id[:8])
        self.assertEqual(code, 0)
        self.assertEqual(out["outcome"], "landed")
        self.assertEqual(out["dest"], str(self.creds / "a.token"))

    def test_landed_while_waiting(self):
        import threading
        import time

        drop_id = self.link()

        def land():
            time.sleep(0.3)
            store = Store(self.state)
            store.record_outcome(store.consume(drop_id), "landed")

        threading.Thread(target=land).start()
        code, out, _ = self.wait(drop_id, "--timeout", "10s")
        self.assertEqual(code, 0)
        self.assertEqual(out["outcome"], "landed")

    def test_a_consumed_link_without_an_outcome_yet_is_given_a_grace_period(self):
        import threading
        import time

        drop_id = self.link()
        store = Store(self.state)
        req = store.consume(drop_id)

        def land():
            time.sleep(0.3)
            Store(self.state).record_outcome(req, "landed")

        threading.Thread(target=land).start()
        code, out, _ = self.wait(drop_id, "--timeout", "10s")
        self.assertEqual(out["outcome"], "landed")

    def test_failed(self):
        drop_id = self.link()
        store = Store(self.state)
        store.record_outcome(store.consume(drop_id), "failed")
        code, out, _ = self.wait(drop_id)
        self.assertEqual(code, 1)
        self.assertEqual(out["outcome"], "failed")

    def test_revoked(self):
        drop_id = self.link()
        self.run_cli("revoke", drop_id)
        code, out, _ = self.wait(drop_id)
        self.assertEqual(code, 1)
        self.assertEqual(out["outcome"], "revoked")

    def test_expired_while_still_in_the_store(self):
        drop_id = self.link()
        store = Store(self.state)
        data = json.loads(store.path.read_text())
        data[drop_id]["expires_at"] = 0
        store.path.write_text(json.dumps(data))
        code, out, _ = self.wait(drop_id)
        self.assertEqual(code, 1)
        self.assertEqual(out["outcome"], "expired")

    def test_timeout(self):
        drop_id = self.link()
        code, out, _ = self.wait(drop_id, "--timeout", "1s")
        self.assertEqual(code, 3)
        self.assertEqual(out["outcome"], "timeout")

    def test_unknown_and_ambiguous(self):
        code, _, err = self.run_cli("wait", "nope")
        self.assertEqual(code, 1)
        self.link()
        self.link()
        code, _, err = self.run_cli("wait", "")
        self.assertEqual(code, 2)
