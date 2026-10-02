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
        self.tmp = Path(tempfile.mkdtemp())
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
