import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from jarvis_bot import application as app
from jarvis_bot import database as jarvis_database


class QarzGuruhRegexFallbackTests(unittest.IsolatedAsyncioTestCase):
    """AI mavjud bo'lmagan holatni simulyatsiya qilib, eski kalit-so'z asosidagi
    zaxira mantiqni tekshiradi — bu real tarmoq/API so'rovi yubormaydi."""

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

        ai_off = patch.object(app, "qarz_ai_tahlil", AsyncMock(return_value=None))
        ai_off.start()
        self.addCleanup(ai_off.stop)

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


class QarzGuruhAiTests(unittest.IsolatedAsyncioTestCase):
    """qarz_ai_tahlil natijasini soxtalashtirib, AI yo'li orkestratsiyasini
    tekshiradi — real OpenAI so'rovi yubormaydi (tez, bepul, deterministik)."""

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
        app.init_delivery_db()
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

    async def test_ai_transaction_is_recorded(self):
        natija = app.QarzXabariNatija(
            qarz_xabarimi=True, dokon_nomi="Chinor 2", harakat="QARZ", summa=452000,
        )
        with patch.object(app, "qarz_ai_tahlil", AsyncMock(return_value=natija)):
            await app.qarz_guruh(self.make_update("Chinor 2 dan 452000 sum qarz berdik"), SimpleNamespace())

        rows = jarvis_database.get_debt_summary()
        self.assertEqual(rows[0], ("Chinor 2", "Haydovchi Ali", 452000))

    async def test_ai_balance_only_message_adjusts_without_new_transaction(self):
        jarvis_database.add_debt("Chinor 2", 300000, "QARZ", "", "Haydovchi Ali")
        natija = app.QarzXabariNatija(
            qarz_xabarimi=True, dokon_nomi="Chinor 2", harakat=None, summa=None, qoldiq=100000,
        )
        with patch.object(app, "qarz_ai_tahlil", AsyncMock(return_value=natija)):
            await app.qarz_guruh(self.make_update("Chinor 2 остатка 100000"), SimpleNamespace())

        rows = jarvis_database.get_debt_summary()
        self.assertEqual(sum(r[2] for r in rows), 100000)

    async def test_ai_non_debt_message_is_ignored(self):
        natija = app.QarzXabariNatija(qarz_xabarimi=False)
        with patch.object(app, "qarz_ai_tahlil", AsyncMock(return_value=natija)):
            await app.qarz_guruh(self.make_update("Salom qalaysiz"), SimpleNamespace())
        self.assertEqual(jarvis_database.get_debt_summary(), [])

    async def test_message_with_no_digits_skips_ai_call(self):
        ai_mock = AsyncMock(return_value=None)
        with patch.object(app, "qarz_ai_tahlil", ai_mock):
            await app.qarz_guruh(self.make_update("Salom qalaysiz"), SimpleNamespace())
        ai_mock.assert_not_awaited()


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
        app.init_delivery_db()

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


class QarzCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.old_path = jarvis_database.DB_PATH
        jarvis_database.DB_PATH = Path(self.directory.name) / "test.db"
        jarvis_database.init_db()
        self.addCleanup(self._restore_db_path)

        base = patch.object(app, "BASE_DIR", Path(self.directory.name))
        base.start()
        self.addCleanup(base.stop)
        app.init_delivery_db()

    def _restore_db_path(self):
        jarvis_database.DB_PATH = self.old_path

    async def test_qarz_command_shows_daily_activity_and_grand_total(self):
        jarvis_database.add_debt("Chinor 2", 500000, "QARZ", "", "Ali")
        jarvis_database.add_debt("Chinor 2", 200000, "TULOV", "", "Ali")
        jarvis_database.add_debt("Bek Market", 300000, "QARZ", "", "Vali")

        sent = {}

        async def reply_text(text, *args, **kwargs):
            sent["text"] = text

        update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
        context = SimpleNamespace(args=[])
        await app.qarz(update, context)

        text = sent["text"]
        self.assertIn("Bismillahir rohmanir rohim", text)
        self.assertIn("Qarzga berildi: 800 000", text)
        self.assertIn("Qaytarildi (to‘lov): 200 000", text)
        self.assertIn("JAMI (barcha do‘konlar bizdan qarzdor): 600 000", text)

    async def test_qarz_bosh_excludes_entries_before_the_cutoff(self):
        con = jarvis_database.connect()
        con.execute(
            "INSERT INTO debts(shop_name, amount, action, employee_name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Eski Do'kon", 999000, "QARZ", "Ali", "2020-01-01 00:00:00"),
        )
        con.commit()
        con.close()
        jarvis_database.add_debt("Yangi Do'kon", 100000, "QARZ", "", "Vali")

        set_update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
        await app.qarz_bosh(set_update, SimpleNamespace(args=[app.uz_today()]))

        sent = {}

        async def reply_text(text, *args, **kwargs):
            sent["text"] = text

        report_update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
        await app.qarz(report_update, SimpleNamespace(args=[]))

        self.assertNotIn("Eski Do'kon", sent["text"])
        self.assertIn("Yangi Do'kon", sent["text"])
        self.assertIn(app.uz_today(), sent["text"])


if __name__ == "__main__":
    unittest.main()
