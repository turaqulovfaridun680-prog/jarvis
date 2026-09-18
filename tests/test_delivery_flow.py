import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from jarvis_bot import application as app


class DeliveryFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = patch.object(app, "BASE_DIR", Path(self.directory.name))
        base.start()
        self.addCleanup(base.stop)
        channel = patch.object(app, "send_delivery_channel", new_callable=AsyncMock)
        self.channel = channel.start()
        self.addCleanup(channel.stop)
        app.init_delivery_db()
        self.message = SimpleNamespace(text="", reply_text=AsyncMock())
        self.query = SimpleNamespace(
            data="yd:1", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=self.message,
        )
        self.update = SimpleNamespace(
            message=self.message, callback_query=None,
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(id=123),
        )
        self.context = SimpleNamespace(user_data={})
        with app.delivery_db() as con:
            con.execute(
                "INSERT INTO delivery_users(telegram_user_id, telegram_chat_id, driver_id) "
                "VALUES ('123', '123', 1)"
            )

    async def start_loading(self, registered=True):
        with patch.object(app, "is_admin_user", return_value=not registered):
            state = await app.yuklash_start(self.update, self.context)
        if not registered:
            self.assertEqual(state, app.Y_DRIVER)
            self.update.callback_query = self.query
            state = await app.yuklash_driver(self.update, self.context)
            response = self.query.edit_message_text.call_args
        else:
            response = self.message.reply_text.call_args
        self.assertEqual(state, app.Y_PRODUCT_SEARCH)
        self.assertNotIn("nechta katta list", response.args[0])
        self.assertIn("/tayyor", response.args[0])
        keyboard = response.kwargs["reply_markup"].inline_keyboard
        self.assertTrue(any(b.callback_data.startswith("yp:") for row in keyboard for b in row))
        self.update.callback_query = None
        return self.context.user_data["delivery"]["id"]

    async def test_loading_then_remaining_and_personal_report(self):
        for registered in (True, False):
            with self.subTest(registered=registered):
                self.channel.reset_mock()
                delivery_id = await self.start_loading(registered)
                self.context.user_data["delivery"].update(
                    product_id=1, product_name=app.PRODUCTS[0], unit="dona"
                )
                for quantity in ("10", "2"):
                    self.message.text = quantity
                    self.assertEqual(await app.yuklash_qty(self.update, self.context), app.Y_PRODUCT_SEARCH)
                if registered:
                    self.message.text = "/tayyor"
                    state = await app.yuklash_finish(self.update, self.context)
                else:
                    self.query.data = "yfinish"
                    self.update.callback_query = self.query
                    state = await app.yuklash_product_callback(self.update, self.context)
                    self.update.callback_query = None
                self.assertEqual(state, app.Y_BIG_TRAYS)
                self.channel.assert_not_awaited()
                with app.delivery_db() as con:
                    self.assertEqual(con.execute("SELECT status FROM deliveries WHERE id=?", (delivery_id,)).fetchone()[0], "DRAFT")
                for invalid in ("-1", "1.5", "abc"):
                    self.message.text = invalid
                    self.assertEqual(await app.yuklash_big_trays(self.update, self.context), app.Y_BIG_TRAYS)
                self.message.text = "5"
                self.assertEqual(await app.yuklash_big_trays(self.update, self.context), app.ConversationHandler.END)
                self.assertIn("katta list: 5 dona", self.message.reply_text.call_args.args[0])
                self.channel.assert_awaited_once()
                self.assertNotIn("delivery", self.context.user_data)
                self.assertEqual(await app.qoldiq_start(self.update, self.context), app.Q_DELIVERY)
                self.query.data = f"qd:{delivery_id}"
                self.update.callback_query = self.query
                self.assertEqual(await app.qoldiq_delivery(self.update, self.context), app.Q_ITEM)
                self.update.callback_query = None
                self.message.text = "2"
                self.assertEqual(await app.qoldiq_item_text(self.update, self.context), app.Q_EMPTY_TRAYS)
                self.message.text = "4"
                self.assertEqual(await app.qoldiq_empty_trays(self.update, self.context), app.ConversationHandler.END)
                await app.mening_hisobot(self.update, self.context)
                report = self.message.reply_text.call_args.args[0]
                self.assertIn("yuklandi 12, qoldi 2, tarqatildi 10", report)
                self.assertIn("Qaytmagan katta list: 1 dona", report)

    async def test_empty_loading_cannot_finish_and_can_be_cancelled(self):
        delivery_id = await self.start_loading()
        self.assertEqual(await app.yuklash_finish(self.update, self.context), app.Y_PRODUCT_SEARCH)
        self.channel.assert_not_awaited()
        await app.delivery_cancel(self.update, self.context)
        with app.delivery_db() as con:
            self.assertEqual(con.execute("SELECT status FROM deliveries WHERE id=?", (delivery_id,)).fetchone()[0], "CANCELLED")

    def test_ready_command_is_registered(self):
        loading, _ = app.delivery_handlers()
        self.assertTrue(any(
            isinstance(handler, app.CommandHandler) and "tayyor" in handler.commands
            for handler in loading.states[app.Y_PRODUCT_SEARCH]
        ))


class YordamCheatSheetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = patch.object(app, "BASE_DIR", Path(self.directory.name))
        base.start()
        self.addCleanup(base.stop)
        app.init_delivery_db()
        self.message = SimpleNamespace(text="", reply_text=AsyncMock())
        self.update = SimpleNamespace(message=self.message)
        self.context = SimpleNamespace(user_data={})

    async def test_yordam_shows_default_text_when_unset(self):
        await app.yordam(self.update, self.context)
        self.assertEqual(
            self.message.reply_text.call_args.args[0], app.DEFAULT_TAMINOTCHI_YORDAM
        )

    async def test_yordam_belgila_updates_text_for_everyone(self):
        self.message.text = "/yordam_belgila SAMSACHA - 50 - dona"
        await app.yordam_belgila(self.update, self.context)
        self.assertIn("SAMSACHA - 50 - dona", self.message.reply_text.call_args.args[0])

        self.message.reply_text.reset_mock()
        await app.yordam(self.update, self.context)
        self.assertEqual(self.message.reply_text.call_args.args[0], "SAMSACHA - 50 - dona")

    async def test_yordam_belgila_without_text_shows_current_value(self):
        self.message.text = "/yordam_belgila"
        await app.yordam_belgila(self.update, self.context)
        self.assertIn(app.DEFAULT_TAMINOTCHI_YORDAM, self.message.reply_text.call_args.args[0])
        self.assertEqual(app.get_setting(app.TAMINOTCHI_YORDAM_KEY), "")
