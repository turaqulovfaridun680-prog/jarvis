import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_bot import application as app


class ParseXomashyoLineTests(unittest.TestCase):
    def test_name_quantity_and_price(self):
        result = app.parse_xomashyo_line("Творог агро 18 кг 360 000")
        self.assertEqual(result, {
            "item_name": "Творог агро", "quantity": 18.0, "unit": "кг", "price": 360000,
        })

    def test_price_only_no_quantity(self):
        result = app.parse_xomashyo_line("Бозор 130 000")
        self.assertEqual(result, {
            "item_name": "Бозор", "quantity": None, "unit": None, "price": 130000,
        })

    def test_name_only_request_without_price(self):
        result = app.parse_xomashyo_line("Тухум")
        self.assertEqual(result, {
            "item_name": "Тухум", "quantity": None, "unit": None, "price": None,
        })

    def test_thousand_grouped_price_without_unit(self):
        result = app.parse_xomashyo_line("Тухумчиба 1 040 000")
        self.assertEqual(result["item_name"], "Тухумчиба")
        self.assertEqual(result["price"], 1040000)
        self.assertIsNone(result["quantity"])

    def test_blank_line_returns_none(self):
        self.assertIsNone(app.parse_xomashyo_line("   "))

    def test_quantity_immediately_attached_to_unit(self):
        result = app.parse_xomashyo_line("Кабатма 5та 30 000")
        self.assertEqual(result["item_name"], "Кабатма")
        self.assertEqual(result["quantity"], 5.0)
        self.assertEqual(result["unit"], "та")
        self.assertEqual(result["price"], 30000)


class BozorGuruhHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = patch.object(app, "BASE_DIR", Path(self.directory.name))
        base.start()
        self.addCleanup(base.stop)
        group_id = patch.object(app, "BOZOR_GROUP_CHAT_ID", "-5206536113")
        group_id.start()
        self.addCleanup(group_id.stop)
        app.init_delivery_db()

    def make_update(self, text, chat_id="-5206536113", first_name="Mulakem"):
        message = SimpleNamespace(
            text=text, message_id=1,
            date=datetime(2026, 9, 2, 8, 13, tzinfo=timezone.utc),
        )
        return SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(first_name=first_name),
        )

    async def test_ignores_messages_outside_bozor_group(self):
        update = self.make_update("Творог агро 18 кг 360 000", chat_id="999")
        await app.bozor_guruh(update, SimpleNamespace())
        with app.delivery_db() as con:
            count = con.execute("SELECT COUNT(*) FROM xomashyo_log").fetchone()[0]
        self.assertEqual(count, 0)

    async def test_logs_each_line_of_a_multiline_message(self):
        text = "Творог агро 18 кг 360 000\nМасло агро 1 кг 100 000\nБозор 130 000"
        update = self.make_update(text)
        await app.bozor_guruh(update, SimpleNamespace())
        with app.delivery_db() as con:
            rows = con.execute(
                "SELECT item_name, quantity, unit, price, work_date FROM xomashyo_log ORDER BY id"
            ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["item_name"], "Творог агро")
        self.assertEqual(rows[0]["price"], 360000)
        self.assertEqual(rows[0]["work_date"], "2026-09-02")

    async def test_report_sums_prices_for_the_day(self):
        await app.bozor_guruh(
            self.make_update("Творог агро 18 кг 360 000"), SimpleNamespace()
        )
        await app.bozor_guruh(
            self.make_update("Тухум"), SimpleNamespace()
        )
        update = SimpleNamespace(
            message=SimpleNamespace(reply_text=self._record_reply()),
        )
        context = SimpleNamespace(args=["2026-09-02"])
        await app.xomashyo_hisobot(update, context)
        self.assertIn("Творог агро", self.sent_text)
        self.assertIn("Тухум", self.sent_text)
        self.assertIn("360 000", self.sent_text)
        self.assertIn("Jami xarajat: 360 000", self.sent_text)

    async def test_report_aggregates_same_item_across_date_range(self):
        await app.bozor_guruh(
            self.make_update("Творог агро 18 кг 360 000"), SimpleNamespace()
        )
        later = self.make_update("Творог агро 2 кг 40 000")
        later.message.date = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
        await app.bozor_guruh(later, SimpleNamespace())

        update = SimpleNamespace(message=SimpleNamespace(reply_text=self._record_reply()))
        context = SimpleNamespace(args=["2026-09-01", "2026-09-05"])
        await app.xomashyo_hisobot(update, context)
        self.assertIn("2026-09-01 — 2026-09-05", self.sent_text)
        self.assertIn("jami 20 кг", self.sent_text)
        self.assertIn("400 000", self.sent_text)
        self.assertIn("2 marta", self.sent_text)

    async def test_report_filters_by_search_term(self):
        await app.bozor_guruh(self.make_update("Творог агро 18 кг 360 000"), SimpleNamespace())
        await app.bozor_guruh(self.make_update("Тухум"), SimpleNamespace())

        update = SimpleNamespace(message=SimpleNamespace(reply_text=self._record_reply()))
        context = SimpleNamespace(args=["2026-09-02", "тухум"])
        await app.xomashyo_hisobot(update, context)
        self.assertIn("Тухум", self.sent_text)
        self.assertNotIn("Творог", self.sent_text)

    def _record_reply(self):
        async def reply_text(text, *args, **kwargs):
            self.sent_text = text
        return reply_text


class KunlikBozorYuborishTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = patch.object(app, "BASE_DIR", Path(self.directory.name))
        base.start()
        self.addCleanup(base.stop)
        admins = patch.object(app, "ADMIN_USER_IDS", frozenset({"111", "222"}))
        admins.start()
        self.addCleanup(admins.stop)
        app.init_delivery_db()

        today = app.uz_today()
        with app.delivery_db() as con:
            con.execute(
                "INSERT INTO xomashyo_log(telegram_chat_id, sender_name, raw_text, "
                "item_name, quantity, unit, price, work_date) "
                "VALUES ('-1', 'Mulakem', 'Тухум 20 000', 'Тухум', NULL, NULL, 20000, ?)",
                (today,),
            )

    async def test_sends_todays_summary_to_every_admin(self):
        sent = []

        async def send_message(chat_id, text):
            sent.append((chat_id, text))

        context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
        await app.kunlik_bozor_yuborish(context)
        self.assertEqual({c for c, _ in sent}, {111, 222})
        self.assertIn("Тухум", sent[0][1])
        self.assertIn("20 000", sent[0][1])


if __name__ == "__main__":
    unittest.main()
