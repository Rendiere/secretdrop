import os
import tempfile
import unittest
from pathlib import Path

from secretdrop.paths import PathRejected, resolve_destination


class ResolveDestinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.allowed = Path(self.tmp) / "creds"
        self.allowed.mkdir()
        self.allowlist = [str(self.allowed / "*"), str(Path(self.tmp) / "ws-*" / ".secrets.env")]

    def test_accepts_path_inside_allowlist(self):
        dest = resolve_destination(str(self.allowed / "bot.token"), self.allowlist)
        self.assertEqual(dest, self.allowed / "bot.token")

    def test_accepts_glob_segment_in_middle(self):
        ws = Path(self.tmp) / "ws-agent"
        ws.mkdir()
        dest = resolve_destination(str(ws / ".secrets.env"), self.allowlist)
        self.assertEqual(dest, ws / ".secrets.env")

    def test_rejects_path_outside_allowlist(self):
        with self.assertRaises(PathRejected):
            resolve_destination(str(Path(self.tmp) / "elsewhere.token"), self.allowlist)

    def test_rejects_dotdot_traversal(self):
        sneaky = str(self.allowed / ".." / "escape.token")
        with self.assertRaises(PathRejected):
            resolve_destination(sneaky, self.allowlist)

    def test_rejects_dotdot_that_resolves_back_inside(self):
        # Even a traversal that lands inside the allowlist is refused outright.
        sneaky = str(self.allowed / "sub" / ".." / "bot.token")
        with self.assertRaises(PathRejected):
            resolve_destination(sneaky, self.allowlist)

    def test_rejects_symlinked_parent_pointing_outside(self):
        outside = Path(self.tmp) / "outside"
        outside.mkdir()
        link = self.allowed / "link"
        os.symlink(outside, link)
        with self.assertRaises(PathRejected):
            resolve_destination(str(link / "bot.token"), self.allowlist)

    def test_rejects_symlinked_destination_pointing_outside(self):
        outside = Path(self.tmp) / "outside.token"
        outside.write_text("")
        link = self.allowed / "bot.token"
        os.symlink(outside, link)
        with self.assertRaises(PathRejected):
            resolve_destination(str(link), self.allowlist)

    def test_rejects_relative_path(self):
        with self.assertRaises(PathRejected):
            resolve_destination("creds/bot.token", self.allowlist)

    def test_star_does_not_cross_a_slash(self):
        nested = self.allowed / "deep"
        nested.mkdir()
        with self.assertRaises(PathRejected):
            resolve_destination(str(nested / "bot.token"), self.allowlist)

    def test_empty_allowlist_rejects_everything(self):
        with self.assertRaises(PathRejected):
            resolve_destination(str(self.allowed / "bot.token"), [])

    def test_expands_tilde_in_allowlist_and_destination(self):
        home = os.path.expanduser("~")
        entry = os.path.join(home, ".secretdrop-test", "*")
        got = resolve_destination("~/.secretdrop-test/a.token", [entry])
        self.assertEqual(got, Path(home) / ".secretdrop-test" / "a.token")


if __name__ == "__main__":
    unittest.main()
