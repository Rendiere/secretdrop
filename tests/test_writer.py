import os
import tempfile
import unittest
from pathlib import Path

from secretdrop.writer import write_secret


class WriteFileModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))

    def test_writes_the_whole_value(self):
        dest = self.tmp / "bot.token"
        write_secret(dest, "file", None, "sk-abc123")
        self.assertEqual(dest.read_text(), "sk-abc123")

    def test_destination_is_0600(self):
        dest = self.tmp / "bot.token"
        write_secret(dest, "file", None, "v")
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)

    def test_tightens_permissions_on_an_existing_world_readable_file(self):
        dest = self.tmp / "bot.token"
        dest.write_text("old")
        os.chmod(dest, 0o644)
        write_secret(dest, "file", None, "new")
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(dest.read_text(), "new")

    def test_creates_parent_directories_as_0700(self):
        dest = self.tmp / "a" / "b" / "bot.token"
        write_secret(dest, "file", None, "v")
        self.assertEqual((self.tmp / "a").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.tmp / "a" / "b").stat().st_mode & 0o777, 0o700)

    def test_leaves_no_temporary_file_behind(self):
        dest = self.tmp / "bot.token"
        write_secret(dest, "file", None, "v")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["bot.token"])

    def test_strips_surrounding_whitespace_from_the_pasted_value(self):
        dest = self.tmp / "bot.token"
        write_secret(dest, "file", None, "  sk-abc\n")
        self.assertEqual(dest.read_text(), "sk-abc")


class WriteEnvModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.dest = self.tmp / ".secrets.env"

    def test_appends_to_a_missing_file(self):
        write_secret(self.dest, "env", "BOT_TOKEN", "abc")
        self.assertEqual(self.dest.read_text(), "BOT_TOKEN=abc\n")

    def test_replaces_an_existing_key_in_place_and_keeps_the_rest(self):
        self.dest.write_text("# comment\nFIRST=1\nBOT_TOKEN=old\nLAST=2\n")
        write_secret(self.dest, "env", "BOT_TOKEN", "new")
        self.assertEqual(
            self.dest.read_text(),
            "# comment\nFIRST=1\nBOT_TOKEN=new\nLAST=2\n",
        )

    def test_appends_when_the_key_is_absent(self):
        self.dest.write_text("FIRST=1\n")
        write_secret(self.dest, "env", "BOT_TOKEN", "new")
        self.assertEqual(self.dest.read_text(), "FIRST=1\nBOT_TOKEN=new\n")

    def test_adds_a_trailing_newline_when_the_file_lacks_one(self):
        self.dest.write_text("FIRST=1")
        write_secret(self.dest, "env", "BOT_TOKEN", "new")
        self.assertEqual(self.dest.read_text(), "FIRST=1\nBOT_TOKEN=new\n")

    def test_does_not_match_a_key_that_is_only_a_prefix(self):
        self.dest.write_text("BOT_TOKEN_OLD=keep\n")
        write_secret(self.dest, "env", "BOT_TOKEN", "new")
        self.assertEqual(self.dest.read_text(), "BOT_TOKEN_OLD=keep\nBOT_TOKEN=new\n")

    def test_replaces_an_exported_key(self):
        self.dest.write_text("export BOT_TOKEN=old\n")
        write_secret(self.dest, "env", "BOT_TOKEN", "new")
        self.assertEqual(self.dest.read_text(), "export BOT_TOKEN=new\n")

    def test_quotes_a_value_containing_whitespace(self):
        write_secret(self.dest, "env", "K", "a b")
        self.assertEqual(self.dest.read_text(), "K='a b'\n")

    def test_replaces_only_the_first_occurrence_and_drops_later_duplicates(self):
        self.dest.write_text("K=one\nOTHER=x\nK=two\n")
        write_secret(self.dest, "env", "K", "new")
        self.assertEqual(self.dest.read_text(), "K=new\nOTHER=x\n")

    def test_env_file_is_0600(self):
        write_secret(self.dest, "env", "K", "v")
        self.assertEqual(self.dest.stat().st_mode & 0o777, 0o600)

    def test_rejects_an_invalid_key(self):
        for bad in ["", "1BAD", "has space", "has=equals", "a\nb"]:
            with self.subTest(key=bad):
                with self.assertRaises(ValueError):
                    write_secret(self.dest, "env", bad, "v")

    def test_rejects_an_empty_value(self):
        with self.assertRaises(ValueError):
            write_secret(self.tmp / "x.token", "file", None, "   ")

    def test_rejects_a_newline_in_an_env_value(self):
        # Otherwise a pasted value could smuggle a second assignment into the file.
        with self.assertRaises(ValueError):
            write_secret(self.dest, "env", "K", "good\nEVIL=1")
        self.assertFalse(self.dest.exists())

    def test_rejects_a_null_byte(self):
        with self.assertRaises(ValueError):
            write_secret(self.tmp / "x.token", "file", None, "a\x00b")

    def test_rejects_an_unknown_mode(self):
        with self.assertRaises(ValueError):
            write_secret(self.tmp / "x.token", "sideways", None, "v")


if __name__ == "__main__":
    unittest.main()
