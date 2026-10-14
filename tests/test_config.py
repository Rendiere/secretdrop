import json
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from secretdrop.config import Config, ConfigError, check_bind, clamp_ttl, load


class BindTests(unittest.TestCase):
    def test_refuses_every_interface(self):
        for address in ["0.0.0.0", "::"]:
            with self.subTest(address=address):
                with self.assertRaises(ConfigError):
                    check_bind(address, allow_unspecified=False)

    def test_allows_it_only_when_asked_explicitly(self):
        self.assertEqual(check_bind("0.0.0.0", allow_unspecified=True), "0.0.0.0")

    def test_allows_an_ordinary_address(self):
        self.assertEqual(check_bind("100.64.0.1", False), "100.64.0.1")
        self.assertEqual(check_bind("127.0.0.1", False), "127.0.0.1")

    def test_load_refuses_an_unspecified_bind(self):
        tmp = Path(tempfile.mkdtemp()) / "config.json"
        tmp.write_text(json.dumps({"bind": "0.0.0.0"}))
        with self.assertRaises(ConfigError):
            load(tmp)


class TtlTests(unittest.TestCase):
    def test_clamps_both_ends(self):
        self.assertEqual(clamp_ttl(1), 60)
        self.assertEqual(clamp_ttl(1800), 1800)
        self.assertEqual(clamp_ttl(10**9), 86400)

    def test_refuses_non_positive(self):
        for bad in [0, -1]:
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    clamp_ttl(bad)


class TailscaleBindTests(unittest.TestCase):
    """A tailnet bind waits for Tailscale and never settles for localhost."""

    def _config(self, bind="tailscale"):
        tmp = Path(tempfile.mkdtemp()) / "config.json"
        tmp.write_text(json.dumps({"bind": bind}))
        return tmp

    def test_load_does_not_fall_back_to_localhost(self):
        with mock.patch("secretdrop.config.tailscale_ip", return_value=None):
            cfg = load(self._config())
        self.assertEqual(cfg.bind_mode, "tailscale")
        self.assertEqual(cfg.bind, "")

    def test_load_takes_the_address_when_tailscale_is_already_up(self):
        with mock.patch("secretdrop.config.tailscale_ip", return_value="100.80.172.19"):
            cfg = load(self._config())
        self.assertEqual(cfg.bind, "100.80.172.19")

    def test_resolve_bind_retries_until_the_address_arrives(self):
        cfg = Config(bind="", bind_mode="tailscale")
        answers = [None, None, "100.80.172.19"]
        with mock.patch("secretdrop.config.tailscale_ip", side_effect=answers) as ip, \
             mock.patch("secretdrop.config.time.sleep") as sleep:
            self.assertEqual(cfg.resolve_bind(timeout_seconds=60), "100.80.172.19")
        self.assertEqual(ip.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(cfg.bind, "100.80.172.19")

    def test_resolve_bind_backs_off(self):
        cfg = Config(bind="", bind_mode="tailscale")
        with mock.patch("secretdrop.config.tailscale_ip", side_effect=[None, None, "100.64.0.1"]), \
             mock.patch("secretdrop.config.time.sleep") as sleep:
            cfg.resolve_bind(timeout_seconds=600)
        waits = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(waits, [1.0, 2.0])

    def test_resolve_bind_gives_up_loudly(self):
        cfg = Config(bind="", bind_mode="tailscale")
        with mock.patch("secretdrop.config.tailscale_ip", return_value=None), \
             mock.patch("secretdrop.config.time.sleep"):
            with self.assertRaises(ConfigError) as caught:
                cfg.resolve_bind(timeout_seconds=5)
        self.assertIn("127.0.0.1", str(caught.exception))
        self.assertEqual(cfg.bind, "")

    def test_resolve_bind_does_not_wait_with_no_timeout(self):
        cfg = Config(bind="", bind_mode="tailscale")
        with mock.patch("secretdrop.config.tailscale_ip", return_value=None) as ip, \
             mock.patch("secretdrop.config.time.sleep") as sleep:
            with self.assertRaises(ConfigError):
                cfg.resolve_bind(timeout_seconds=0)
        self.assertEqual(ip.call_count, 1)
        sleep.assert_not_called()

    def test_resolve_bind_leaves_a_literal_address_alone(self):
        cfg = Config(bind="127.0.0.1")
        with mock.patch("secretdrop.config.tailscale_ip") as ip:
            self.assertEqual(cfg.resolve_bind(timeout_seconds=60), "127.0.0.1")
        ip.assert_not_called()


class HostTests(unittest.TestCase):
    def test_allowed_hosts_cover_the_bind_address(self):
        cfg = Config(bind="100.64.0.1", port=7391)
        self.assertIn("100.64.0.1:7391", cfg.allowed_hosts)
        self.assertNotIn("evil.example.com", cfg.allowed_hosts)

    def test_allowed_hosts_cover_a_base_url(self):
        cfg = Config(bind="127.0.0.1", port=7391, base_url="http://pi.tailnet.ts.net:7391")
        self.assertIn("pi.tailnet.ts.net:7391", cfg.allowed_hosts)
        self.assertEqual(cfg.url_base, "http://pi.tailnet.ts.net:7391")


if __name__ == "__main__":
    unittest.main()
