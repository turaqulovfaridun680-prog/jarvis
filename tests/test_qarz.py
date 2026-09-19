import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_bot import application as app
from jarvis_bot import database as jarvis_database


class QarzGuruhTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.old_path = jarvis_database.DB_PATH
        jarvis_database.DB_PATH = Path(self.directory.name) / "test.db"
        jarvis_database.init_db()
        self.addCleanup(self._restore_db_path)

        group_id = patch.object(app, "DEBT_GROUP_CHAT_ID", "-5026417146")
        group_id.start()
        self.addCleanup(group_id.stop)
        base = patch.object(app, "BASE_DIR", Path(self.directory.name))
        base.start()
        self.addCleanup(base.stop)
        self._message_id = 0

    def _restore_db_path(self):
        jarvis_database.DB_PATH = self.old_path

    def make_update(self, text, chat_id="-5026417146", first_name="Haydovchi Ali"):
        self._message_id += 1
        return SimpleNamespace(
            message=SimpleNamespace(text=text, message_id=self._message_id),
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(first_name=first_name),
        )

    async def test_non_admin_employee_can_record_shop_debt(self):
        # Do'konga yuk berildi, do'kon shuncha summaga qarzdor bo'ldi.
        update = self.make_update("Chinor 2 dan 452.000 сум qarz berdik")
        await app.qarz_guruh(update, SimpleNamespace())

        rows = jarvis_database.get_debt_summary()
        self.assertEqual(len(rows), 1)
        shop_name, employee_name, balance = rows[0]
        self.assertEqual(shop_name, "Chinor 2")
        self.assertEqual(employee_name, "Haydovchi Ali")
        self.assertEqual(balance, 452000)

    async def test_payment_received_reduces_shop_balance(self):
        await app.qarz_guruh(self.make_update("Chinor 2 dan 452.000 сум qarz berdik"), SimpleNamespace())
        # Do'kondan qarzning bir qismi qaytarib olindi.
        await app.qarz_guruh(self.make_update("Chinor 2 dan 200.000 сум oldim"), SimpleNamespace())

        rows = jarvis_database.get_debt_summary()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 252000)

    async def test_message_without_trigger_word_is_ignored(self):
        update = self.make_update("Chinor 2 dan 452.000 сум yukladik, hammasi joyida")
        await app.qarz_guruh(update, SimpleNamespace())
        self.assertEqual(jarvis_database.get_debt_summary(), [])

    async def test_message_outside_debt_group_is_ignored(self):
        update = self.make_update("Chinor 2 dan 452.000 сум qarz berdik", chat_id="999")
        await app.qarz_guruh(update, SimpleNamespace())
        self.assertEqual(jarvis_database.get_debt_summary(), [])


if __name__ == "__main__":
    unittest.main()
