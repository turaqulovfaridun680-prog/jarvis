import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "jarvis.db"


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_db():
    con = connect()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            telegram_chat_id TEXT UNIQUE,
            role TEXT,
            duties TEXT,
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            key TEXT,
            value TEXT,
            UNIQUE(category, key)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_name TEXT,
            task TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_chat_id TEXT,
            role TEXT,
            message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_name TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount >= 0),
            action TEXT NOT NULL CHECK(action IN ('QARZ', 'TULOV')),
            note TEXT,
            employee_name TEXT,
            telegram_chat_id TEXT,
            telegram_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(telegram_chat_id, telegram_message_id)
        )
    """)

    # Eski bazalarni ma'lumot yo'qotmasdan yangi sxemaga o'tkazish.
    columns = {row[1] for row in cur.execute("PRAGMA table_info(debts)")}
    if "employee_name" not in columns:
        cur.execute("ALTER TABLE debts ADD COLUMN employee_name TEXT")
    if "telegram_chat_id" not in columns:
        cur.execute("ALTER TABLE debts ADD COLUMN telegram_chat_id TEXT")
    if "telegram_message_id" not in columns:
        cur.execute("ALTER TABLE debts ADD COLUMN telegram_message_id INTEGER")
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_debts_telegram_message
        ON debts(telegram_chat_id, telegram_message_id)
        WHERE telegram_chat_id IS NOT NULL AND telegram_message_id IS NOT NULL
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_chat_id ON chat_history(telegram_chat_id, id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_debts_shop_name ON debts(shop_name)")

    con.commit()
    con.close()


def add_or_update_employee(name, telegram_chat_id=None, role=None, duties=None, notes=None):
    con = connect()
    cur = con.cursor()

    # Mavjud Telegram hisobi bo'lsa o'sha yozuv yangilanadi. Oddiy ism
    # boshqa odamning rol va ma'lumotlarini egallash uchun identifikator emas.
    row = None
    if telegram_chat_id is not None:
        cur.execute("SELECT id FROM employees WHERE telegram_chat_id=?", (str(telegram_chat_id),))
        row = cur.fetchone()

    if row is None and telegram_chat_id is None:
        cur.execute("SELECT id FROM employees WHERE lower(name)=lower(?)", (name,))
        row = cur.fetchone()

    if row:
        cur.execute("""
            UPDATE employees
            SET telegram_chat_id = COALESCE(?, telegram_chat_id),
                role = COALESCE(?, role),
                duties = COALESCE(?, duties),
                notes = COALESCE(?, notes)
            WHERE id = ?
        """, (telegram_chat_id, role, duties, notes, row[0]))
    else:
        cur.execute("""
            INSERT INTO employees
            (name, telegram_chat_id, role, duties, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (name, telegram_chat_id, role, duties, notes))

    con.commit()
    con.close()


def get_employee_by_name(name):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        SELECT name, telegram_chat_id, role, duties, notes
        FROM employees
        WHERE lower(name)=lower(?)
    """, (name,))

    row = cur.fetchone()
    con.close()

    if not row:
        return None

    return {
        "name": row[0],
        "telegram_chat_id": row[1],
        "role": row[2],
        "duties": row[3],
        "notes": row[4]
    }


def get_employee_by_chat_id(chat_id):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        SELECT name, telegram_chat_id, role, duties, notes
        FROM employees
        WHERE telegram_chat_id = ?
    """, (str(chat_id),))

    row = cur.fetchone()
    con.close()

    if not row:
        return None

    return {
        "name": row[0],
        "telegram_chat_id": row[1],
        "role": row[2],
        "duties": row[3],
        "notes": row[4]
    }


def remember(category, key, value):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO memories(category, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(category, key)
        DO UPDATE SET value=excluded.value
    """, (category, key, value))

    con.commit()
    con.close()


def recall(category, key):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        SELECT value
        FROM memories
        WHERE category=? AND key=?
    """, (category, key))

    row = cur.fetchone()
    con.close()

    return row[0] if row else None


def add_task(employee_name, task):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO tasks(employee_name, task)
        VALUES (?, ?)
    """, (employee_name, task))

    con.commit()
    con.close()


def save_chat(chat_id, role, message):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO chat_history
        (telegram_chat_id, role, message)
        VALUES (?, ?, ?)
    """, (str(chat_id), role, message))

    con.commit()
    con.close()


def get_recent_chat(chat_id, limit=20):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        SELECT role, message
        FROM chat_history
        WHERE telegram_chat_id=?
        ORDER BY id DESC
        LIMIT ?
    """, (str(chat_id), limit))

    rows = cur.fetchall()
    con.close()

    rows.reverse()
    return rows
def add_debt(shop_name, amount, action, note="", employee_name="", telegram_chat_id=None, telegram_message_id=None):
    con = connect()
    cur = con.cursor()

    if action not in {"QARZ", "TULOV"}:
        con.close()
        raise ValueError("Noto'g'ri qarz harakati")
    amount = int(amount)
    if amount < 0:
        con.close()
        raise ValueError("Summa manfiy bo'lishi mumkin emas")
    cur.execute("""
        INSERT OR IGNORE INTO debts
        (shop_name, amount, action, note, employee_name, telegram_chat_id, telegram_message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (shop_name.strip(), amount, action, note, employee_name,
          str(telegram_chat_id) if telegram_chat_id is not None else None,
          telegram_message_id))

    con.commit()
    con.close()


def get_debt_balance(shop_name=None):
    con = connect()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_name TEXT NOT NULL,
            amount INTEGER NOT NULL,
            action TEXT NOT NULL,
            note TEXT,
            employee_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    if shop_name:
        cur.execute("""
            SELECT action, amount
            FROM debts
            WHERE LOWER(shop_name) = LOWER(?)
        """, (shop_name,))
    else:
        cur.execute("""
            SELECT action, amount
            FROM debts
        """)

    rows = cur.fetchall()
    con.close()

    balance = 0

    for action, amount in rows:
        if action == "QARZ":
            balance += amount
        elif action == "TULOV":
            balance -= amount

    return balance
def get_debt_summary():
    con = connect()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_name TEXT NOT NULL,
            amount INTEGER NOT NULL,
            action TEXT NOT NULL,
            note TEXT,
            employee_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        SELECT shop_name, COALESCE(employee_name, ''),
               SUM(CASE
                   WHEN action = 'QARZ' THEN amount
                   WHEN action = 'TULOV' THEN -amount
                   ELSE 0
               END)
        FROM debts
        GROUP BY shop_name, employee_name
    """)

    rows = cur.fetchall()
    con.close()
    return rows


if __name__ == "__main__":
    init_db()
    print("JARVIS database tayyor:", DB_PATH)
