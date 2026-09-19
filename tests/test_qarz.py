import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


class QarzYozFlowTests(unittest.IsolatedAsyncioTestCase):
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

        self.message = SimpleNamespace(
            text="", message_id=1, reply_text=AsyncMock(),
        )
        self.query = SimpleNamespace(
            data="", answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        self.update = SimpleNamespace(
            message=self.message, callback_query=self.query,
            effective_chat=SimpleNamespace(id="-5026417146"),
            effective_user=SimpleNamespace(first_name="Sotuvchi Vali"),
        )
        self.context = SimpleNamespace(user_data={})

    def _restore_db_path(self):
        jarvis_database.DB_PATH = self.old_path

    async def test_full_flow_records_debt_for_existing_shop(self):
        jarvis_database.add_debt("Chinor 2", 100000, "QARZ", "boshlang'ich", "Sotuvchi Vali")

        state = await app.qarz_yoz_start(self.update, self.context)
        self.assertEqual(state, app.QY_SHOP)
        keyboard = self.message.reply_text.call_args.kwargs["reply_markup"].inline_keyboard
        self.assertTrue(any(b.text == "Chinor 2" for row in keyboard for b in row))

        self.query.data = "qzs:0"
        state = await app.qarz_yoz_shop(self.update, self.context)
        self.assertEqual(state, app.QY_ACTION)
        self.assertEqual(self.context.user_data["qarz_shop_name"], "Chinor 2")

        self.query.data = "qzt:QARZ"
        state = await app.qarz_yoz_action(self.update, self.context)
        self.assertEqual(state, app.QY_AMOUNT)

        self.message.text = "50 000"
        state = await app.qarz_yoz_amount(self.update, self.context)
        self.assertEqual(state, app.ConversationHandler.END)

        rows = jarvis_database.get_debt_summary()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Chinor 2")
        self.assertEqual(rows[0][2], 150000)
        report = self.message.reply_text.call_args.args[0]
        self.assertIn("50 000", report)
        self.assertIn("150 000", report)

    async def test_new_shop_and_payment_are_recorded(self):
        state = await app.qarz_yoz_start(self.update, self.context)
        self.assertEqual(state, app.QY_SHOP)

        self.query.data = "qznew"
        state = await app.qarz_yoz_shop(self.update, self.context)
        self.assertEqual(state, app.QY_NEW_SHOP)

        self.message.text = "Yangi Bozor"
        state = await app.qarz_yoz_new_shop(self.update, self.context)
        self.assertEqual(state, app.QY_ACTION)

        self.query.data = "qzt:TULOV"
        state = await app.qarz_yoz_action(self.update, self.context)
        self.assertEqual(state, app.QY_AMOUNT)

        self.message.text = "30000"
        state = await app.qarz_yoz_amount(self.update, self.context)
        self.assertEqual(state, app.ConversationHandler.END)

        rows = jarvis_database.get_debt_summary()
        self.assertEqual(rows[0], ("Yangi Bozor", "Sotuvchi Vali", -30000))

    async def test_invalid_amount_is_rejected_and_stays_in_state(self):
        self.context.user_data["qarz_shop_name"] = "Chinor 2"
        self.context.user_data["qarz_action"] = "QARZ"

        self.message.text = "abc"
        state = await app.qarz_yoz_amount(self.update, self.context)
        self.assertEqual(state, app.QY_AMOUNT)
        self.assertEqual(jarvis_database.get_debt_summary(), [])

    async def test_cancel_button_ends_conversation_without_saving(self):
        self.query.data = "qzcancel"
        state = await app.qarz_yoz_shop(self.update, self.context)
        self.assertEqual(state, app.ConversationHandler.END)
        self.assertEqual(jarvis_database.get_debt_summary(), [])

    async def test_command_outside_debt_group_is_rejected(self):
        self.update.effective_chat = SimpleNamespace(id="999")
        state = await app.qarz_yoz_start(self.update, self.context)
        self.assertEqual(state, app.ConversationHandler.END)
        self.message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
