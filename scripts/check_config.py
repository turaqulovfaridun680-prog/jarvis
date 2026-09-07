"""Maxfiy qiymatlarni chiqarmasdan server konfiguratsiyasini tekshiradi."""

import sqlite3
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY")
RECOMMENDED = (
    "ADMIN_USER_IDS",
    "DEBT_GROUP_CHAT_ID",
    "DRIVER_REGISTRATION_SECRET",
    "ZILOLA_CHAT_ID",
    "DASTAVKA_REPORT_CHAT_ID",
)


def main():
    values = dotenv_values(ROOT / ".env")
    errors = [key for key in REQUIRED if not values.get(key)]
    warnings = [key for key in RECOMMENDED if not values.get(key)]

    database = ROOT / "jarvis.db"
    if database.exists():
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as con:
            print("Database:", con.execute("PRAGMA quick_check").fetchone()[0])
    else:
        print("Database: birinchi ishga tushishda yaratiladi")

    print("Majburiy sozlamalar:", "OK" if not errors else ", ".join(errors) + " yo'q")
    print("Ixtiyoriy modullar:", "OK" if not warnings else ", ".join(warnings) + " yo'q")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
