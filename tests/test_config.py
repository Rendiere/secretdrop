import json
import tempfile
import unittest
from pathlib import Path

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
