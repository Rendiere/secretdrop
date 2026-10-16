import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "examples" / "openclaw-wake.sh"


class OpenClawWakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.args_out = self.tmp / "args.json"
        fake = self.tmp / "openclaw"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"json.dump(sys.argv[1:], open({str(self.args_out)!r}, 'w'))\n"
        )
        fake.chmod(0o755)

    def run_script(self, **env):
        base = {
            "PATH": f"{self.tmp}:/usr/bin:/bin",
            "SECRETDROP_ID": "abc123",
            "SECRETDROP_LABEL": "Telegram bot token",
            "SECRETDROP_DEST": "/home/you/.openclaw/credentials/bot.token",
            "SECRETDROP_MODE": "file",
            "SECRETDROP_KEY": "",
        }
        base.update(env)
        subprocess.run(["sh", str(SCRIPT)], env=base, check=True, cwd=self.tmp)
        return json.loads(self.args_out.read_text())

    def test_a_session_is_passed_as_the_session_key(self):
        args = self.run_script(SECRETDROP_SESSION="agent:main:telegram:group:-100123")
        self.assertEqual(args[:2], ["system", "event"])
        self.assertEqual(args[args.index("--session-key") + 1], "agent:main:telegram:group:-100123")
        self.assertEqual(args[args.index("--mode") + 1], "now")

    def test_no_session_omits_the_session_key(self):
        args = self.run_script()
        self.assertNotIn("--session-key", args)
        self.assertIn("Telegram bot token", args[args.index("--text") + 1])

    def test_a_hostile_label_is_one_literal_argument(self):
        args = self.run_script(SECRETDROP_LABEL="it's $(touch pwned) `touch pwned2`")
        self.assertIn("$(touch pwned)", args[args.index("--text") + 1])
        self.assertFalse((self.tmp / "pwned").exists())
        self.assertFalse((self.tmp / "pwned2").exists())


if __name__ == "__main__":
    unittest.main()
