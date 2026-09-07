import asyncio
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from jarvis_bot import application
from jarvis_bot import database as jarvis_database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_path = jarvis_database.DB_PATH
        jarvis_database.DB_PATH = Path(self.temp_dir.name) / "test.db"
        jarvis_database.init_db()

    def tearDown(self):
        jarvis_database.DB_PATH = self.old_path
        self.temp_dir.cleanup()

    def test_fresh_database_has_debt_table(self):
        self.assertEqual(jarvis_database.get_debt_summary(), [])

    def test_same_name_does_not_take_another_account(self):
        jarvis_database.add_or_update_employee("Ali", "100", role="admin")
        jarvis_database.add_or_update_employee("Ali", "200")
        self.assertEqual(jarvis_database.get_employee_by_chat_id("100")["role"], "admin")
        self.assertIsNone(jarvis_database.get_employee_by_chat_id("200")["role"])

    def test_telegram_message_is_idempotent(self):
        args = ("Chinor", 1000, "QARZ", "xabar", "Ali", "-1", 10)
        jarvis_database.add_debt(*args)
        jarvis_database.add_debt(*args)
        con = jarvis_database.connect()
        try:
            count = con.execute("SELECT COUNT(*) FROM debts").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 1)

    def test_split_shift_is_eight_hours(self):
        today = datetime.now().strftime("%d/%m/%Y")
        records = [
            {"Vaqt belgisi": f"{today} 08:00:00", "Xodim": "Ali", "Harakat": "KELDIM"},
            {"Vaqt belgisi": f"{today} 12:00:00", "Xodim": "Ali", "Harakat": "KETDIM"},
            {"Vaqt belgisi": f"{today} 14:00:00", "Xodim": "Ali", "Harakat": "KELDIM"},
            {"Vaqt belgisi": f"{today} 18:00:00", "Xodim": "Ali", "Harakat": "KETDIM"},
        ]
        worksheet = SimpleNamespace(get_all_records=lambda: records)
        spreadsheet = SimpleNamespace(worksheet=lambda _name: worksheet)
        original = application.gspread.service_account
        application.gspread.service_account = lambda **_kwargs: SimpleNamespace(
            open=lambda _name: spreadsheet
        )
        try:
            report = application.get_davomat()
        finally:
            application.gspread.service_account = original
        self.assertIn("Jami ishladi: 8 soat 0 daqiqa", report)


class ApplicationTests(unittest.TestCase):
    def test_application_builds_all_handler_groups(self):
        app = application.build_application()
        self.assertGreaterEqual(sum(len(items) for items in app.handlers.values()), 15)

    def test_number_validation_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf", "1e309"):
            with self.assertRaises(ValueError):
                application.clean_number(value)

    def test_long_message_is_split(self):
        sent = []

        async def reply_text(text):
            sent.append(text)

        message = SimpleNamespace(reply_text=reply_text)
        asyncio.run(application.send_long_message(message, "a" * 8001, limit=3800))
        self.assertEqual("".join(sent), "a" * 8001)
        self.assertTrue(all(len(part) <= 3800 for part in sent))


if __name__ == "__main__":
    unittest.main()
