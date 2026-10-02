import tempfile
import time
import unittest
from pathlib import Path

from secretdrop.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(Path(self.tmp) / "state" / "pending.json")

    def _create(self, **kw):
        args = dict(dest="/tmp/x.token", label="Test token", mode="file", key=None, ttl_seconds=600)
        args.update(kw)
        return self.store.create(**args)

    def test_create_returns_unguessable_id(self):
        a = self._create()
        b = self._create()
        self.assertNotEqual(a.id, b.id)
        self.assertGreaterEqual(len(a.id), 22)

    def test_get_returns_the_request(self):
        req = self._create(label="Telegram bot token")
        got = self.store.get(req.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.label, "Telegram bot token")

    def test_consume_is_single_use(self):
        req = self._create()
        self.assertIsNotNone(self.store.consume(req.id))
        self.assertIsNone(self.store.consume(req.id))
        self.assertIsNone(self.store.get(req.id))

    def test_expired_request_is_not_returned(self):
        req = self._create(ttl_seconds=-1)
        self.assertIsNone(self.store.get(req.id))
        self.assertIsNone(self.store.consume(req.id))

    def test_expiry_is_enforced_at_the_boundary(self):
        req = self._create(ttl_seconds=1)
        self.assertIsNotNone(self.store.get(req.id))
        time.sleep(1.1)
        self.assertIsNone(self.store.get(req.id))

    def test_unknown_id_returns_none(self):
        self.assertIsNone(self.store.get("nope"))
        self.assertIsNone(self.store.consume("nope"))

    def test_revoke_removes_the_request(self):
        req = self._create()
        self.assertTrue(self.store.revoke(req.id))
        self.assertIsNone(self.store.get(req.id))
        self.assertFalse(self.store.revoke(req.id))

    def test_list_pending_skips_expired(self):
        live = self._create(label="live")
        self._create(label="dead", ttl_seconds=-1)
        labels = [r.label for r in self.store.list_pending()]
        self.assertEqual(labels, ["live"])
        self.assertEqual(self.store.get(live.id).label, "live")

    def test_state_file_is_private(self):
        self._create()
        mode = self.store.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)

    def test_store_never_holds_a_value_field(self):
        self._create()
        raw = self.store.path.read_text()
        self.assertNotIn("value", raw)
        self.assertNotIn("secret", raw)

    def test_state_survives_a_new_store_instance(self):
        req = self._create(label="persisted")
        other = Store(self.store.path)
        self.assertEqual(other.get(req.id).label, "persisted")


if __name__ == "__main__":
    unittest.main()
