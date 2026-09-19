import os
import asyncio
import math
import re
import sqlite3
import tempfile
import gspread
from datetime import datetime, timezone, timedelta, time as dt_time
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Literal

from openai import OpenAI
from pydantic import BaseModel

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .database import (
    init_db,
    add_or_update_employee,
    get_employee_by_chat_id,
    get_employee_by_name,
    remember,
    recall,
    save_chat,
    get_recent_chat,
    add_debt,
     get_debt_balance,
    get_debt_summary,
    get_debt_shops,
    get_debt_daily_summary,
)
from .config import ROOT_DIR, settings

BASE_DIR = ROOT_DIR
OPENAI_API_KEY = settings.openai_api_key
TELEGRAM_BOT_TOKEN = settings.telegram_bot_token
ZILOLA_CHAT_ID = settings.zilola_chat_id
DASTAVKA_REPORT_CHAT_ID = settings.delivery_report_chat_id
DEBT_GROUP_CHAT_ID = settings.debt_group_chat_id
BOZOR_GROUP_CHAT_ID = settings.bozor_group_chat_id
DRIVER_REGISTRATION_SECRET = settings.driver_registration_secret
ADMIN_USER_IDS = settings.admin_user_ids
GOOGLE_CREDENTIALS_FILE = settings.google_credentials_file

client = OpenAI(api_key=OPENAI_API_KEY)

# ==================== DASTAVCHIK MODULI ====================

DRIVERS = [
    "Ravshanbek",
    "Baxodir aka",
    "Redvan",
    "Asadbek",
    "Farrux",
    "Farid",
    "SORO Dastavka 201",
    "SORO Dastavka 202",
]
PRODUCTS = [
    "SAMSACHA", "KEKS", "KURASAN", "TVAROJNI", "NAPALON", "MEDOVIK",
    "BANANCHIK", "PAXLAVA", "AVGANKA", "PONCHIK", "SHAYBA", "NEMO",
    "RAGALIK", "DO'NDIRMA", "BANAN MOLOKO", "ROMASHKA", "BANTIK",
    "MURAVENIK", "PISOCHNI GUL", "MISHKA BIZE", "TURBICHKA BIK",
    "TURBICHKA ASAL", "TURBICHKA PUDRA", "ZAVARNOY", "EKLER",
    "SOLNISHKA", "GEMOGLABIN", "KURABE", "BARAKCHA", "RULET",
]
UNITS = {
    "karobka": "📦 Karobka",
    "kg": "⚖️ Kilogramm",
    "dona": "🔢 Dona",
    "konteyner": "🧺 Konteyner",
}

TAMINOTCHI_YORDAM_KEY = "yordam_taminochi"
DEFAULT_TAMINOTCHI_YORDAM = (
    "📋 Ta'minotchi uchun namuna (Bozor guruhida yozing):\n\n"
    "Xomashyo nomi, miqdori va narxi bilan yozing.\n\n"
    "Masalan:\n"
    "Творог агро 18 кг 360 000\n"
    "Масло агро 1 кг 100 000\n"
    "Бозор 130 000\n\n"
    "Har bir xomashyoni alohida qatorda yozing — bot avtomatik qayd qilib boradi."
)

(
    Y_DRIVER, Y_BIG_TRAYS, Y_PRODUCT_SEARCH, Y_NEW_PRODUCT,
    Y_UNIT, Y_QTY, Q_DELIVERY, Q_ITEM, Q_EMPTY_TRAYS,
) = range(9)


@contextmanager
def delivery_db():
    con = sqlite3.connect(BASE_DIR / "jarvis.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    try:
        with con:
            yield con
    finally:
        con.close()


def init_delivery_db():
    with delivery_db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS delivery_drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS delivery_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_date TEXT NOT NULL,
                driver_id INTEGER NOT NULL,
                telegram_chat_id TEXT NOT NULL,
                big_trays_loaded INTEGER NOT NULL DEFAULT 0 CHECK(big_trays_loaded >= 0),
                big_trays_empty_returned INTEGER CHECK(big_trays_empty_returned >= 0),
                status TEXT NOT NULL DEFAULT 'DRAFT'
                    CHECK(status IN ('DRAFT', 'OPEN', 'CLOSED', 'CANCELLED')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                FOREIGN KEY(driver_id) REFERENCES delivery_drivers(id)
            );
            CREATE TABLE IF NOT EXISTS delivery_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                unit TEXT NOT NULL CHECK(unit IN ('karobka', 'kg', 'dona', 'konteyner')),
                loaded_qty REAL NOT NULL CHECK(loaded_qty > 0),
                remaining_qty REAL CHECK(remaining_qty >= 0),
                UNIQUE(delivery_id, product_id, unit),
                FOREIGN KEY(delivery_id) REFERENCES deliveries(id) ON DELETE CASCADE,
                FOREIGN KEY(product_id) REFERENCES delivery_products(id)
            );
            CREATE TABLE IF NOT EXISTS delivery_users (
                telegram_user_id TEXT PRIMARY KEY,
                telegram_chat_id TEXT NOT NULL,
                driver_id INTEGER NOT NULL,
                registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(driver_id) REFERENCES delivery_drivers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_deliveries_date_status
                ON deliveries(work_date, status);
            CREATE INDEX IF NOT EXISTS idx_delivery_items_delivery
                ON delivery_items(delivery_id);
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS xomashyo_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id TEXT NOT NULL,
                message_id INTEGER,
                sender_name TEXT,
                raw_text TEXT NOT NULL,
                item_name TEXT NOT NULL,
                quantity REAL,
                unit TEXT,
                price INTEGER,
                work_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_xomashyo_log_date
                ON xomashyo_log(work_date);
        """)
        # Eski bazalarni ma'lumot yo'qotmasdan yangi sxemaga o'tkazish.
        xomashyo_columns = {row[1] for row in con.execute("PRAGMA table_info(xomashyo_log)")}
        if "category" not in xomashyo_columns:
            con.execute(
                "ALTER TABLE xomashyo_log ADD COLUMN category TEXT NOT NULL DEFAULT 'xarid'"
            )
        con.executemany(
            "INSERT OR IGNORE INTO delivery_drivers(name) VALUES (?)",
            [(name,) for name in DRIVERS],
        )
        con.executemany(
            "INSERT OR IGNORE INTO delivery_products(name) VALUES (?)",
            [(name,) for name in PRODUCTS],
        )


def get_setting(key, default=""):
    with delivery_db() as con:
        row = con.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with delivery_db() as con:
        con.execute(
            "INSERT INTO bot_settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def uz_today():
    return datetime.now(timezone(timedelta(hours=5))).strftime("%Y-%m-%d")


def clean_number(value):
    text = str(value).strip().replace(" ", "").replace(",", ".")
    number = float(text)
    if not math.isfinite(number) or number < 0:
        raise ValueError
    return number


def fmt_qty(value):
    value = float(value or 0)
    return str(int(value)) if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")


def driver_keyboard():
    with delivery_db() as con:
        rows = con.execute(
            "SELECT id, name FROM delivery_drivers WHERE active=1 ORDER BY id"
        ).fetchall()
    buttons = [[InlineKeyboardButton(r["name"], callback_data=f"yd:{r['id']}")] for r in rows]
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="ycancel")])
    return InlineKeyboardMarkup(buttons)


def registration_driver_keyboard():
    with delivery_db() as con:
        rows = con.execute(
            "SELECT id, name FROM delivery_drivers WHERE active=1 ORDER BY id"
        ).fetchall()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(r["name"], callback_data=f"regd:{r['id']}")]
        for r in rows
    ])


def delivery_user(telegram_user_id):
    with delivery_db() as con:
        return con.execute("""
            SELECT du.*, dr.name AS driver_name
            FROM delivery_users du
            JOIN delivery_drivers dr ON dr.id=du.driver_id
            WHERE du.telegram_user_id=?
        """, (str(telegram_user_id),)).fetchone()


def is_admin_user(user):
    if not user:
        return False
    if str(user.id) in ADMIN_USER_IDS:
        return True
    employee = get_employee_by_chat_id(user.id)
    return bool(
        employee
        and str(employee.get("role") or "").strip().lower()
        in {"admin", "owner", "director", "rahbar"}
    )


def admin_only(handler):
    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if is_admin_user(user):
            return await handler(update, context)
        if user and delivery_user(user.id):
            await update.effective_message.reply_text(
                "🚚 Sizga faqat dastavka bo‘limi ochiq.\n\n"
                "/yuklash — ertalabki yuk\n"
                "/qoldiq — kun oxiri qoldig‘i\n"
                "/mening_hisobot — o‘z hisobotingiz\n"
                "/yordam — ta'minotchi uchun namuna"
            )
            return
        await update.effective_message.reply_text("⛔ Bu bo‘lim faqat administrator uchun.")
        return None
    return wrapped


async def send_delivery_channel(context, text):
    if not DASTAVKA_REPORT_CHAT_ID:
        return
    try:
        await context.bot.send_message(chat_id=DASTAVKA_REPORT_CHAT_ID, text=text)
    except Exception as exc:
        print("DASTAVKA KANALIGA YUBORISH XATOSI:", exc)


async def register_driver_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.pop("driver_registration_allowed", False):
        await query.edit_message_text("⛔ Ro‘yxatdan o‘tish havolasi yaroqsiz yoki eskirgan.")
        return
    driver_id = int(query.data.split(":")[1])
    with delivery_db() as con:
        driver = con.execute(
            "SELECT name FROM delivery_drivers WHERE id=? AND active=1", (driver_id,)
        ).fetchone()
        if not driver:
            await query.edit_message_text("❌ Dastavchik topilmadi.")
            return
        con.execute("""
            INSERT INTO delivery_users(telegram_user_id, telegram_chat_id, driver_id)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                telegram_chat_id=excluded.telegram_chat_id,
                driver_id=excluded.driver_id,
                registered_at=CURRENT_TIMESTAMP
        """, (str(update.effective_user.id), str(update.effective_chat.id), driver_id))
    await context.bot.set_my_commands(
        [
            BotCommand("yuklash", "Ertalab mashinaga yuklash"),
            BotCommand("qoldiq", "Kun oxiri qoldig‘i"),
            BotCommand("mening_hisobot", "Bugungi shaxsiy hisobot"),
            BotCommand("bekor", "Amalni bekor qilish"),
        ],
        scope=BotCommandScopeChat(chat_id=update.effective_chat.id),
    )
    await query.edit_message_text(
        f"✅ Siz {driver['name']} sifatida ro‘yxatdan o‘tdingiz.\n\n"
        "Sizga faqat dastavka bo‘limi ochiq:\n"
        "/yuklash — ertalabki yuk\n"
        "/qoldiq — kun oxiri qoldig‘i\n"
        "/mening_hisobot — o‘z hisobotingiz"
    )


async def dastavchik_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not DRIVER_REGISTRATION_SECRET:
        await update.message.reply_text("DRIVER_REGISTRATION_SECRET sozlanmagan.")
        return
    me = await context.bot.get_me()
    await update.message.reply_text(
        "🚚 Dastavchiklarga mana shu linkni yuboring:\n\n"
        f"https://t.me/{me.username}?start=dastavchik_{DRIVER_REGISTRATION_SECRET}\n\n"
        "Ular START bosib, o‘z nomini tanlaydi."
    )


async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Ushbu chat yoki kanal ID raqami:\n{update.effective_chat.id}"
    )


def unit_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"yu:{key}") for key, label in list(UNITS.items())[:2]],
        [InlineKeyboardButton(label, callback_data=f"yu:{key}") for key, label in list(UNITS.items())[2:]],
        [InlineKeyboardButton("⬅️ Mahsulotga qaytish", callback_data="yback_product")],
    ])


def product_results_keyboard(rows):
    buttons = []
    for i in range(0, len(rows), 2):
        buttons.append([
            InlineKeyboardButton(r["name"], callback_data=f"yp:{r['id']}")
            for r in rows[i:i + 2]
        ])
    buttons.extend([
        [InlineKeyboardButton("➕ Yangi mahsulot", callback_data="ynew")],
        [InlineKeyboardButton("✅ Yuklash tugadi", callback_data="yfinish")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="ycancel")],
    ])
    return InlineKeyboardMarkup(buttons)


def get_delivery_summary(delivery_id, closed=False):
    with delivery_db() as con:
        delivery = con.execute("""
            SELECT d.*, dr.name AS driver_name
            FROM deliveries d JOIN delivery_drivers dr ON dr.id=d.driver_id
            WHERE d.id=?
        """, (delivery_id,)).fetchone()
        items = con.execute("""
            SELECT p.name, i.unit, i.loaded_qty, i.remaining_qty
            FROM delivery_items i JOIN delivery_products p ON p.id=i.product_id
            WHERE i.delivery_id=? ORDER BY i.id
        """, (delivery_id,)).fetchall()
    if not delivery:
        return "❌ Hisobot topilmadi."
    lines = [
        "📊 SORO — DASTAVCHIK KUN YAKUNI" if closed else "✅ SORO — YUKLASH HISOBOTI",
        "",
        f"📅 Sana: {delivery['work_date']}",
        f"👤 Dastavchik: {delivery['driver_name']}",
        "",
        "📦 Mahsulotlar:",
    ]
    for item in items:
        if closed:
            remaining = float(item["remaining_qty"] or 0)
            sold = max(0, float(item["loaded_qty"]) - remaining)
            lines.append(
                f"• {item['name']}: yuklandi {fmt_qty(item['loaded_qty'])}, "
                f"qoldi {fmt_qty(remaining)}, tarqatildi {fmt_qty(sold)} {UNITS[item['unit']].split(' ', 1)[1].lower()}"
            )
        else:
            lines.append(
                f"• {item['name']} — {fmt_qty(item['loaded_qty'])} "
                f"{UNITS[item['unit']].split(' ', 1)[1].lower()}"
            )
    lines.extend(["", f"🍽 Olib ketilgan katta list: {delivery['big_trays_loaded']} dona"])
    if closed:
        returned = int(delivery["big_trays_empty_returned"] or 0)
        difference = int(delivery["big_trays_loaded"]) - returned
        lines.extend([
            f"✅ Bo‘sh qaytgan katta list: {returned} dona",
            f"⚖️ Qaytmagan katta list: {difference} dona",
        ])
        if difference < 0:
            lines.append("⚠️ Bo‘sh list olib ketilgandan ko‘p kiritilgan.")
    return "\n".join(lines)


async def yuklash_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"/yuklash HANDLER: {__name__}.yuklash_start | {Path(__file__).resolve()}", flush=True)
    context.user_data.pop("delivery", None)
    registered = None if is_admin_user(update.effective_user) else delivery_user(update.effective_user.id)
    if registered:
        context.user_data["delivery"] = {
            "driver_id": registered["driver_id"],
            "driver_name": registered["driver_name"],
        }
        return await yuklash_begin_products(update, context)
    await update.message.reply_text(
        "📦 Mahsulot yuklash boshlandi.\n\n🚚 Dastavchikni tanlang:", reply_markup=driver_keyboard()
    )
    return Y_DRIVER


async def yuklash_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    driver_id = int(query.data.split(":")[1])
    with delivery_db() as con:
        driver = con.execute("SELECT name FROM delivery_drivers WHERE id=?", (driver_id,)).fetchone()
    context.user_data["delivery"] = {"driver_id": driver_id, "driver_name": driver["name"]}
    return await yuklash_begin_products(update, context)


async def yuklash_begin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data["delivery"]
    with delivery_db() as con:
        cur = con.execute("""
            INSERT INTO deliveries(work_date, driver_id, telegram_chat_id, big_trays_loaded)
            VALUES (?, ?, ?, 0)
        """, (uz_today(), data["driver_id"], str(update.effective_chat.id)))
        data["id"] = cur.lastrowid
        rows = con.execute(
            "SELECT id, name FROM delivery_products WHERE active=1 ORDER BY name"
        ).fetchall()
    text = (
        "📦 Mahsulot yuklash boshlandi.\n\n"
        f"👤 Dastavchik: {data['driver_name']}\n\n"
        "📦 Mahsulotni tanlang yoki nomini yozing.\n"
        "Yangi mahsulot uchun «yangi» deb yozing.\n\n"
        "Yakunlash uchun «Yuklash tugadi» tugmasini bosing yoki /tayyor yozing."
    )
    target = update.callback_query.edit_message_text if update.callback_query else update.message.reply_text
    await target(text, reply_markup=product_results_keyboard(rows))
    return Y_PRODUCT_SEARCH


async def yuklash_big_trays(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
        if qty < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ 0 yoki musbat butun son kiriting. Masalan: 75")
        return Y_BIG_TRAYS
    data = context.user_data["delivery"]
    with delivery_db() as con:
        con.execute(
            "UPDATE deliveries SET big_trays_loaded=?, status='OPEN' WHERE id=?",
            (qty, data["id"]),
        )
    text = get_delivery_summary(data["id"])
    await update.message.reply_text(text)
    await send_delivery_channel(context, text)
    context.user_data.pop("delivery", None)
    return ConversationHandler.END


async def yuklash_product_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    term = update.message.text.strip()
    if term.lower() in {"yangi", "+", "new"}:
        await update.message.reply_text("✍️ Yangi mahsulotning to‘liq nomini yozing:")
        return Y_NEW_PRODUCT
    with delivery_db() as con:
        rows = con.execute("""
            SELECT id, name FROM delivery_products
            WHERE active=1 AND name LIKE ? COLLATE NOCASE
            ORDER BY name LIMIT 20
        """, (term + "%",)).fetchall()
    if not rows:
        await update.message.reply_text(
            "❌ Mahsulot topilmadi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Yangi mahsulot", callback_data="ynew")],
                [InlineKeyboardButton("✅ Yuklash tugadi", callback_data="yfinish")],
            ]),
        )
        return Y_PRODUCT_SEARCH
    await update.message.reply_text(
        f"🔎 «{term.upper()}» bo‘yicha topildi:",
        reply_markup=product_results_keyboard(rows),
    )
    return Y_PRODUCT_SEARCH


async def yuklash_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "yfinish":
        return await yuklash_finish(update, context)
    await query.answer()
    data = query.data
    if data == "ynew":
        await query.edit_message_text("✍️ Yangi mahsulotning to‘liq nomini yozing:")
        return Y_NEW_PRODUCT
    product_id = int(data.split(":")[1])
    with delivery_db() as con:
        product = con.execute("SELECT name FROM delivery_products WHERE id=?", (product_id,)).fetchone()
    context.user_data["delivery"].update(product_id=product_id, product_name=product["name"])
    await query.edit_message_text(
        f"📦 {product['name']}\n\nO‘lchov birligini tanlang:", reply_markup=unit_keyboard()
    )
    return Y_UNIT


async def yuklash_new_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(update.message.text.strip().upper().split())
    if len(name) < 2 or len(name) > 60:
        await update.message.reply_text("❌ Mahsulot nomi 2–60 ta belgidan iborat bo‘lsin.")
        return Y_NEW_PRODUCT
    with delivery_db() as con:
        con.execute("INSERT OR IGNORE INTO delivery_products(name) VALUES (?)", (name,))
        row = con.execute("SELECT id, name FROM delivery_products WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    context.user_data["delivery"].update(product_id=row["id"], product_name=row["name"])
    await update.message.reply_text(
        f"✅ {row['name']} ro‘yxatga qo‘shildi.\n\nO‘lchov birligini tanlang:",
        reply_markup=unit_keyboard(),
    )
    return Y_UNIT


async def yuklash_unit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "yback_product":
        await query.edit_message_text("🔎 Mahsulot nomi yoki bosh harfini yozing:")
        return Y_PRODUCT_SEARCH
    unit = query.data.split(":")[1]
    context.user_data["delivery"]["unit"] = unit
    await query.edit_message_text(
        f"📦 {context.user_data['delivery']['product_name']}\n"
        f"📏 {UNITS[unit]}\n\nMiqdorini kiriting:"
    )
    return Y_QTY


async def yuklash_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = clean_number(update.message.text)
        if qty <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("❌ 0 dan katta miqdor kiriting. Masalan: 12 yoki 5,5")
        return Y_QTY
    data = context.user_data["delivery"]
    with delivery_db() as con:
        con.execute("""
            INSERT INTO delivery_items(delivery_id, product_id, unit, loaded_qty)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(delivery_id, product_id, unit)
            DO UPDATE SET loaded_qty=loaded_qty + excluded.loaded_qty
        """, (data["id"], data["product_id"], data["unit"], qty))
    await update.message.reply_text(
        f"✅ {data['product_name']} — {fmt_qty(qty)} "
        f"{UNITS[data['unit']].split(' ', 1)[1].lower()} qo‘shildi.\n\n"
        "🔎 Keyingi mahsulot nomi yoki bosh harfini yozing.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yuklash tugadi", callback_data="yfinish")],
        ]),
    )
    return Y_PRODUCT_SEARCH


async def yuklash_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    target = query.edit_message_text if query else update.message.reply_text
    if query:
        await query.answer()
    data = context.user_data.get("delivery", {})
    delivery_id = data.get("id")
    if not delivery_id:
        await target("❌ Faol yuklash topilmadi.")
        return ConversationHandler.END
    with delivery_db() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM delivery_items WHERE delivery_id=?", (delivery_id,)
        ).fetchone()[0]
        if count == 0:
            await target("❌ Kamida bitta mahsulot kiriting.")
            return Y_PRODUCT_SEARCH
    await target("🍽 Mashina nechta katta list yuklandi?")
    return Y_BIG_TRAYS


async def qoldiq_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registered = delivery_user(update.effective_user.id)
    with delivery_db() as con:
        if registered:
            rows = con.execute("""
                SELECT d.id, dr.name, d.created_at
                FROM deliveries d JOIN delivery_drivers dr ON dr.id=d.driver_id
                WHERE d.status='OPEN' AND d.driver_id=?
                ORDER BY d.id DESC
                LIMIT 20
            """, (registered["driver_id"],)).fetchall()
        else:
            rows = con.execute("""
                SELECT d.id, dr.name, d.created_at
                FROM deliveries d JOIN delivery_drivers dr ON dr.id=d.driver_id
                WHERE d.status='OPEN'
                ORDER BY d.id DESC
                LIMIT 20
            """).fetchall()
    if not rows:
        await update.message.reply_text("Bugun yakunlanmagan dastavchik yuki topilmadi.")
        return ConversationHandler.END
    buttons = [[InlineKeyboardButton(
        f"{r['name']} — {str(r['created_at'])[:16]}", callback_data=f"qd:{r['id']}"
    )] for r in rows]
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="qcancel")])
    await update.message.reply_text(
        "🌙 Kun oxiri qoldig‘i. Dastavchikni tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return Q_DELIVERY


async def qoldiq_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    delivery_id = int(query.data.split(":")[1])
    with delivery_db() as con:
        items = con.execute("""
            SELECT i.id, p.name, i.unit, i.loaded_qty
            FROM delivery_items i JOIN delivery_products p ON p.id=i.product_id
            WHERE i.delivery_id=? ORDER BY i.id
        """, (delivery_id,)).fetchall()
    context.user_data["closing"] = {
        "delivery_id": delivery_id,
        "items": [dict(r) for r in items],
        "index": 0,
    }
    await query.edit_message_text("📦 Endi mashinada qolgan mahsulotlarni kiriting.")
    return await ask_next_remaining(query.message, context)


async def ask_next_remaining(message, context):
    data = context.user_data["closing"]
    if data["index"] >= len(data["items"]):
        await message.reply_text("🍽 Nechta bo‘sh katta list qaytib keldi?")
        return Q_EMPTY_TRAYS
    item = data["items"][data["index"]]
    await message.reply_text(
        f"📦 {item['name']}\n"
        f"Ertalab yuklandi: {fmt_qty(item['loaded_qty'])} "
        f"{UNITS[item['unit']].split(' ', 1)[1].lower()}\n\n"
        "🚚 Mashinada qancha qoldi?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("0 — qolmadi", callback_data=f"qr:{item['id']}")],
            [InlineKeyboardButton("❌ Bekor qilish", callback_data="qcancel")],
        ]),
    )
    return Q_ITEM


async def save_remaining(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_value):
    data = context.user_data.get("closing")
    if not data:
        return ConversationHandler.END
    item = data["items"][data["index"]]
    if update.callback_query:
        callback_item_id = int(update.callback_query.data.split(":")[1])
        if callback_item_id != item["id"]:
            await update.callback_query.answer(
                "Bu eski tugma. Hozirgi mahsulot uchun javob bering.", show_alert=True
            )
            return Q_ITEM
    try:
        qty = clean_number(raw_value)
        if qty > float(item["loaded_qty"]):
            raise ValueError
    except (ValueError, TypeError):
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(
            f"❌ Qoldiq 0 dan {fmt_qty(item['loaded_qty'])} gacha bo‘lishi kerak."
        )
        return Q_ITEM
    with delivery_db() as con:
        con.execute("UPDATE delivery_items SET remaining_qty=? WHERE id=?", (qty, item["id"]))
    data["index"] += 1
    target = update.callback_query.message if update.callback_query else update.message
    return await ask_next_remaining(target, context)


async def qoldiq_item_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await save_remaining(update, context, update.message.text)


async def qoldiq_item_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await save_remaining(update, context, "0")


async def qoldiq_empty_trays(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        returned = int(update.message.text.strip())
        if returned < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ 0 yoki musbat butun son kiriting. Masalan: 55")
        return Q_EMPTY_TRAYS
    data = context.user_data["closing"]
    with delivery_db() as con:
        delivery = con.execute(
            "SELECT big_trays_loaded FROM deliveries WHERE id=?", (data["delivery_id"],)
        ).fetchone()
        con.execute("""
            UPDATE deliveries
            SET big_trays_empty_returned=?, status='CLOSED', closed_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (returned, data["delivery_id"]))
    text = get_delivery_summary(data["delivery_id"], closed=True)
    if returned != int(delivery["big_trays_loaded"]):
        text += "\n\n⚠️ Katta listlar sonida farq bor — tekshirish kerak."
    await update.message.reply_text(text)
    await send_delivery_channel(context, text)
    context.user_data.pop("closing", None)
    return ConversationHandler.END


async def delivery_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    draft_id = context.user_data.get("delivery", {}).get("id")
    if draft_id:
        with delivery_db() as con:
            con.execute("UPDATE deliveries SET status='CANCELLED' WHERE id=? AND status='DRAFT'", (draft_id,))
    context.user_data.pop("delivery", None)
    context.user_data.pop("closing", None)
    target = query.message if query else update.message
    await target.reply_text("❌ Amal bekor qilindi.")
    return ConversationHandler.END


async def delivery_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with delivery_db() as con:
        rows = con.execute("""
            SELECT id, status FROM deliveries
            WHERE work_date=? AND status IN ('OPEN', 'CLOSED') ORDER BY id
        """, (uz_today(),)).fetchall()
    if not rows:
        await update.message.reply_text("Bugungi dastavchik hisoboti topilmadi.")
        return
    for row in rows:
        await send_long_message(
            update.message,
            get_delivery_summary(row["id"], closed=row["status"] == "CLOSED"),
        )


async def mening_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    registered = delivery_user(update.effective_user.id)
    if not registered:
        await update.message.reply_text("❌ Siz dastavchik sifatida ro‘yxatdan o‘tmagansiz.")
        return
    with delivery_db() as con:
        rows = con.execute("""
            SELECT id, status FROM deliveries
            WHERE work_date=? AND driver_id=? ORDER BY id
        """, (uz_today(), registered["driver_id"])).fetchall()
    if not rows:
        await update.message.reply_text("Bugungi hisobotingiz hali mavjud emas.")
        return
    for row in rows:
        await send_long_message(
            update.message,
            get_delivery_summary(row["id"], closed=row["status"] == "CLOSED"),
        )


def delivery_handlers():
    yuklash = ConversationHandler(
        entry_points=[CommandHandler("yuklash", yuklash_start)],
        states={
            Y_DRIVER: [CallbackQueryHandler(yuklash_driver, pattern=r"^yd:\d+$")],
            Y_BIG_TRAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, yuklash_big_trays)],
            Y_PRODUCT_SEARCH: [
                CommandHandler("tayyor", yuklash_finish),
                CallbackQueryHandler(yuklash_product_callback, pattern=r"^(yp:\d+|ynew|yfinish)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, yuklash_product_search),
            ],
            Y_NEW_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, yuklash_new_product)],
            Y_UNIT: [CallbackQueryHandler(yuklash_unit, pattern=r"^(yu:|yback_product)")],
            Y_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, yuklash_qty)],
        },
        fallbacks=[
            CallbackQueryHandler(delivery_cancel, pattern=r"^ycancel$"),
            CommandHandler("bekor", delivery_cancel),
        ],
        allow_reentry=True,
    )
    qoldiq = ConversationHandler(
        entry_points=[CommandHandler("qoldiq", qoldiq_start)],
        states={
            Q_DELIVERY: [CallbackQueryHandler(qoldiq_delivery, pattern=r"^qd:\d+$")],
            Q_ITEM: [
                CallbackQueryHandler(qoldiq_item_button, pattern=r"^qr:\d+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, qoldiq_item_text),
            ],
            Q_EMPTY_TRAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, qoldiq_empty_trays)],
        },
        fallbacks=[
            CallbackQueryHandler(delivery_cancel, pattern=r"^qcancel$"),
            CommandHandler("bekor", delivery_cancel),
        ],
        allow_reentry=True,
    )
    return yuklash, qoldiq

SORO_CONTEXT = """
Sen SORO JARVIS ismli shaxsiy AI yordamchisan.

Qoidalar:
- Asosan o'zbek tilida gaplash.
- Qisqa, aniq va amaliy javob ber.
- Aniq ma'lumot bo'lmasa, o'ylab topma.
- SORO shirinlik korxonasi Samarqandda faoliyat yuritadi.
- Xodimlar, vazifalar, topshiriqlar, savdo va ishlab chiqarish bo'yicha yordam ber.
"""
def get_zilola_report():
    gc = gspread.service_account(filename=str(GOOGLE_CREDENTIALS_FILE))
    sh = gc.open("SORO — ZILOLA KUNLIK HISOBOTLAR")
    records = sh.sheet1.get_all_records()

    if not records:
        return "Zilolaning hisoboti topilmadi."

    last = records[-1]
    text = "📋 ZILOLA — OXIRGI HISOBOT\n\n"

    for key, value in last.items():
        if value not in ("", None):
            text += f"• {key}: {value}\n"

    return text


def get_keldi_ketdi():
    gc = gspread.service_account(filename=str(GOOGLE_CREDENTIALS_FILE))
    sh = gc.open("soro xodimlar keldi ketdi jadvali")
    ws = sh.worksheet("Shaklga javoblar (1)")
    records = ws.get_all_records()

    if not records:
        return "Keldi-ketdi ma’lumoti topilmadi."

    text = "📋 SORO — KELDI-KETDI\n\n"

    for row in records[-30:]:
        text += (
            f"• {row.get('Vaqt belgisi', '')} | "
            f"{row.get('Xodim', '')} | "
            f"{row.get('Harakat', '')}\n"
        )

    return text

def get_davomat():
    gc = gspread.service_account(filename=str(GOOGLE_CREDENTIALS_FILE))
    sh = gc.open("soro xodimlar keldi ketdi jadvali")
    ws = sh.worksheet("Shaklga javoblar (1)")
    records = ws.get_all_records()

    bugun = datetime.now().strftime("%d/%m/%Y")
    xodimlar = {}

    for row in records:
        vaqt_matn = str(row.get("Vaqt belgisi", "")).strip()
        xodim = str(row.get("Xodim", "")).strip()
        harakat = str(row.get("Harakat", "")).strip().upper()

        if not vaqt_matn.startswith(bugun) or not xodim:
            continue

        try:
            vaqt = datetime.strptime(vaqt_matn, "%d/%m/%Y %H:%M:%S")
        except:
            continue

        if xodim not in xodimlar:
            xodimlar[xodim] = {"keldi": None, "ketdi": None, "events": []}

        if harakat in {"KELDIM", "KETDIM"}:
            xodimlar[xodim]["events"].append((vaqt, harakat))

        if harakat == "KELDIM":
            if xodimlar[xodim]["keldi"] is None:
                xodimlar[xodim]["keldi"] = vaqt

        elif harakat == "KETDIM":
            xodimlar[xodim]["ketdi"] = vaqt

    text = "📊 SORO — BUGUNGI DAVOMAT\n\n"

    for xodim, info in xodimlar.items():
        keldi = info["keldi"]
        ketdi = info["ketdi"]

        text += f"👤 {xodim}\n"

        if keldi:
            text += f"🟢 Keldi: {keldi.strftime('%H:%M')}\n"

            chegara = keldi.replace(hour=8, minute=0, second=0)
            if keldi > chegara:
                kech = int((keldi - chegara).total_seconds() // 60)
                text += f"⏰ Kechikdi: {kech} daqiqa\n"

        if ketdi:
            text += f"🔴 Ketdi: {ketdi.strftime('%H:%M')}\n"

        if keldi and ketdi:
            jami = 0
            ochiq = None
            for event_time, event_type in sorted(info["events"]):
                if event_type == "KELDIM" and ochiq is None:
                    ochiq = event_time
                elif event_type == "KETDIM" and ochiq is not None and event_time >= ochiq:
                    jami += int((event_time - ochiq).total_seconds() // 60)
                    ochiq = None
            jami_soat = jami // 60
            jami_daqiqa = jami % 60

            text += f"⏱ Jami ishladi: {jami_soat} soat {jami_daqiqa} daqiqa\n"

            chegara_ketish = ketdi.replace(hour=18, minute=0, second=0)

            if ketdi > chegara_ketish:
                qoshimcha = int((ketdi - chegara_ketish).total_seconds() // 60)
                text += f"➕ Qo‘shimcha: {qoshimcha // 60} soat {qoshimcha % 60} daqiqa\n"

            elif ketdi < chegara_ketish:
                erta = int((chegara_ketish - ketdi).total_seconds() // 60)
                text += f"⚠️ Erta ketdi: {erta} daqiqa\n"

        text += "\n"

    if not xodimlar:
        return "Bugun hali davomat ma’lumoti yo‘q."

    return text
def get_haftalik():
    gc = gspread.service_account(filename=str(GOOGLE_CREDENTIALS_FILE))
    sh = gc.open("soro xodimlar keldi ketdi jadvali")
    ws = sh.worksheet("Shaklga javoblar (1)")
    records = ws.get_all_records()

    bugun = datetime.now()
    hafta_boshi = bugun - timedelta(days=bugun.weekday())
    hafta_boshi = hafta_boshi.replace(hour=0, minute=0, second=0, microsecond=0)

    xodimlar = {}

    for row in records:
        vaqt_matn = str(row.get("Vaqt belgisi", "")).strip()
        xodim = str(row.get("Xodim", "")).strip()
        harakat = str(row.get("Harakat", "")).strip().upper()

        if not xodim:
            continue

        try:
            vaqt = datetime.strptime(vaqt_matn, "%d/%m/%Y %H:%M:%S")
        except:
            continue

        if vaqt < hafta_boshi or vaqt > bugun:
            continue

        sana = vaqt.strftime("%d.%m.%Y")

        if xodim not in xodimlar:
            xodimlar[xodim] = {}

        if sana not in xodimlar[xodim]:
            xodimlar[xodim][sana] = {"keldi": None, "ketdi": None, "events": []}

        if harakat in {"KELDIM", "KETDIM"}:
            xodimlar[xodim][sana]["events"].append((vaqt, harakat))

        if harakat == "KELDIM":
            if xodimlar[xodim][sana]["keldi"] is None:
                xodimlar[xodim][sana]["keldi"] = vaqt

        elif harakat == "KETDIM":
            xodimlar[xodim][sana]["ketdi"] = vaqt

    if not xodimlar:
        return "Bu hafta hali davomat ma’lumoti yo‘q."

    text = "📊 SORO — HAFTALIK DAVOMAT\n\n"

    for xodim, kunlar in xodimlar.items():
        jami_ish = 0
        jami_kech = 0
        jami_erta = 0
        jami_qoshimcha = 0

        text += f"👤 {xodim}\n"

        for sana, info in kunlar.items():
            keldi = info["keldi"]
            ketdi = info["ketdi"]

            text += f"📅 {sana}\n"

            if keldi:
                text += f"🟢 Keldi: {keldi.strftime('%H:%M')}\n"

                ish_boshi = keldi.replace(hour=8, minute=0, second=0)
                if keldi > ish_boshi:
                    kech = int((keldi - ish_boshi).total_seconds() // 60)
                    jami_kech += kech
                    text += f"⏰ Kechikdi: {kech} daqiqa\n"

            if ketdi:
                text += f"🔴 Ketdi: {ketdi.strftime('%H:%M')}\n"
            else:
                text += "🔴 Ketdi: hali belgilanmagan\n"

            if keldi and ketdi:
                ishladi = 0
                ochiq = None
                for event_time, event_type in sorted(info["events"]):
                    if event_type == "KELDIM" and ochiq is None:
                        ochiq = event_time
                    elif event_type == "KETDIM" and ochiq is not None and event_time >= ochiq:
                        ishladi += int((event_time - ochiq).total_seconds() // 60)
                        ochiq = None
                jami_ish += ishladi
                text += f"⏱ Ishladi: {ishladi // 60} soat {ishladi % 60} daqiqa\n"

                ish_oxiri = ketdi.replace(hour=18, minute=0, second=0)

                if ketdi > ish_oxiri:
                    qoshimcha = int((ketdi - ish_oxiri).total_seconds() // 60)
                    jami_qoshimcha += qoshimcha
                    text += f"➕ Qo‘shimcha: {qoshimcha // 60} soat {qoshimcha % 60} daqiqa\n"

                elif ketdi < ish_oxiri:
                    erta = int((ish_oxiri - ketdi).total_seconds() // 60)
                    jami_erta += erta
                    text += f"⚠️ Erta ketdi: {erta} daqiqa\n"

            text += "\n"

        text += "📌 HAFTALIK JAMI:\n"
        text += f"⏱ Jami ishladi: {jami_ish // 60} soat {jami_ish % 60} daqiqa\n"
        text += f"⏰ Jami kechikish: {jami_kech} daqiqa\n"
        text += f"⚠️ Jami erta ketish: {jami_erta} daqiqa\n"
        text += f"➕ Jami qo‘shimcha: {jami_qoshimcha // 60} soat {jami_qoshimcha % 60} daqiqa\n\n"

    return text
def get_oylik():
    gc = gspread.service_account(filename=str(GOOGLE_CREDENTIALS_FILE))
    sh = gc.open("soro xodimlar keldi ketdi jadvali")
    ws = sh.worksheet("Shaklga javoblar (1)")
    records = ws.get_all_records()

    bugun = datetime.now()
    oy = bugun.month
    yil = bugun.year

    xodimlar = {}

    for row in records:
        vaqt_matn = str(row.get("Vaqt belgisi", "")).strip()
        xodim = str(row.get("Xodim", "")).strip()
        harakat = str(row.get("Harakat", "")).strip().upper()

        if not xodim:
            continue

        try:
            vaqt = datetime.strptime(vaqt_matn, "%d/%m/%Y %H:%M:%S")
        except:
            continue

        if vaqt.month != oy or vaqt.year != yil:
            continue

        sana = vaqt.strftime("%d.%m.%Y")

        if xodim not in xodimlar:
            xodimlar[xodim] = {}

        if sana not in xodimlar[xodim]:
            xodimlar[xodim][sana] = {"keldi": None, "ketdi": None, "events": []}

        if harakat in {"KELDIM", "KETDIM"}:
            xodimlar[xodim][sana]["events"].append((vaqt, harakat))

        if harakat == "KELDIM":
            if xodimlar[xodim][sana]["keldi"] is None:
                xodimlar[xodim][sana]["keldi"] = vaqt

        elif harakat == "KETDIM":
            xodimlar[xodim][sana]["ketdi"] = vaqt

    if not xodimlar:
        return "Bu oy hali davomat ma’lumoti yo‘q."

    text = f"📊 SORO — OYLIK DAVOMAT ({bugun.strftime('%m.%Y')})\n\n"

    for xodim, kunlar in xodimlar.items():
        jami_ish = 0
        jami_kech = 0
        jami_erta = 0
        jami_qoshimcha = 0
        ishlagan_kun = 0

        for sana, info in kunlar.items():
            keldi = info["keldi"]
            ketdi = info["ketdi"]

            if keldi:
                ish_boshi = keldi.replace(hour=8, minute=0, second=0)

                if keldi > ish_boshi:
                    jami_kech += int(
                        (keldi - ish_boshi).total_seconds() // 60
                    )

            if keldi and ketdi:
                ishlagan_kun += 1

                ishladi = 0
                ochiq = None
                for event_time, event_type in sorted(info["events"]):
                    if event_type == "KELDIM" and ochiq is None:
                        ochiq = event_time
                    elif event_type == "KETDIM" and ochiq is not None and event_time >= ochiq:
                        ishladi += int((event_time - ochiq).total_seconds() // 60)
                        ochiq = None
                jami_ish += ishladi

                ish_oxiri = ketdi.replace(
                    hour=18, minute=0, second=0
                )

                if ketdi > ish_oxiri:
                    jami_qoshimcha += int(
                        (ketdi - ish_oxiri).total_seconds() // 60
                    )

                elif ketdi < ish_oxiri:
                    jami_erta += int(
                        (ish_oxiri - ketdi).total_seconds() // 60
                    )

        text += f"👤 {xodim}\n"
        text += f"📅 Ishlagan kun: {ishlagan_kun}\n"
        text += f"⏱ Jami ishladi: {jami_ish // 60} soat {jami_ish % 60} daqiqa\n"
        text += f"⏰ Jami kechikish: {jami_kech} daqiqa\n"
        text += f"⚠️ Jami erta ketish: {jami_erta} daqiqa\n"
        text += f"➕ Jami qo‘shimcha: {jami_qoshimcha // 60} soat {jami_qoshimcha % 60} daqiqa\n\n"

    return text

def hozirgi_vaqt():


    uz_tz = timezone(timedelta(hours=5))
    hozir = datetime.now(uz_tz)
    return hozir.strftime("%d.%m.%Y %H:%M")


async def send_long_message(message, text, limit=3800):
    """Telegram chegarasidan uzun matnni qatorlar bo'yicha xavfsiz bo'ladi."""
    remaining = str(text)
    while remaining:
        if len(remaining) <= limit:
            await message.reply_text(remaining)
            return
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        await message.reply_text(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")


def tarix_matni(chat_id):
    rows = get_recent_chat(chat_id, limit=20)

    qatorlar = []

    for role, message in rows:
        kim = "Foydalanuvchi" if role == "user" else "JARVIS"
        qatorlar.append(f"{kim}: {message}")

    return "\n".join(qatorlar)


async def yordam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_setting(TAMINOTCHI_YORDAM_KEY, DEFAULT_TAMINOTCHI_YORDAM))


async def yordam_belgila(update: Update, context: ContextTypes.DEFAULT_TYPE):
    matn = update.message.text.partition(" ")[2].strip()
    if not matn:
        await update.message.reply_text(
            "✍️ Yangi shpargalka matnini shu buyruqdan keyin yozing.\n\n"
            "Hozirgi matn:\n\n" + get_setting(TAMINOTCHI_YORDAM_KEY, DEFAULT_TAMINOTCHI_YORDAM)
        )
        return
    set_setting(TAMINOTCHI_YORDAM_KEY, matn)
    await update.message.reply_text("✅ /yordam matni yangilandi:\n\n" + matn)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ism = update.effective_user.first_name or "foydalanuvchi"

    expected_driver_token = (
        f"dastavchik_{DRIVER_REGISTRATION_SECRET}" if DRIVER_REGISTRATION_SECRET else None
    )
    if context.args and expected_driver_token and context.args[0] == expected_driver_token:
        context.user_data["driver_registration_allowed"] = True
        await update.message.reply_text(
            "🚚 SORO dastavka tizimiga xush kelibsiz.\n\n"
            "O‘z nomingizni tanlang:",
            reply_markup=registration_driver_keyboard(),
        )
        return

    registered = delivery_user(update.effective_user.id)
    if registered:
        await update.message.reply_text(
            f"Salom, {registered['driver_name']}! 🚚\n\n"
            "Sizga faqat dastavka bo‘limi ochiq:\n"
            "/yuklash — ertalabki yuk\n"
            "/qoldiq — kun oxiri qoldig‘i\n"
            "/mening_hisobot — o‘z hisobotingiz\n"
            "/yordam — ta'minotchi uchun namuna"
        )
        return

    # Agar bu chat_id bazada hali yo'q bo'lsa, ism bilan vaqtincha yozamiz
    employee = get_employee_by_chat_id(chat_id)

    if not employee:
        add_or_update_employee(
            name=ism,
            telegram_chat_id=str(chat_id)
        )

    await update.message.reply_text(
        f"Salom {ism}! Men SORO JARVISman 🤖\n\n"
        f"Sizning chat_id: {chat_id}\n\n"
        "Endi suhbatlar va muhim ma'lumotlar doimiy bazada saqlanadi."
    )


async def tozala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Doimiy xotirani avtomatik o'chirmayman. "
        "Kerak bo'lsa alohida boshqaruv buyrug'i qo'shamiz."
    )


async def kimman(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    employee = get_employee_by_chat_id(chat_id)

    if not employee:
        await update.message.reply_text(
            "Siz haqingizdagi ma'lumot bazada topilmadi."
        )
        return

    javob = f"Ism: {employee['name']}"

    if employee["role"]:
        javob += f"\nLavozim: {employee['role']}"

    if employee["duties"]:
        javob += f"\nVazifalar: {employee['duties']}"

    if employee["notes"]:
        javob += f"\nIzoh: {employee['notes']}"

    await update.message.reply_text(javob)


async def zilolaga_yubor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ZILOLA_CHAT_ID:
        await update.message.reply_text(
            "Zilolaning chat_id si topilmadi."
        )
        return

    matn = " ".join(context.args).strip()

    if not matn:
        await update.message.reply_text(
            "Masalan:\n/zilola Bugun 1 ta tort buyurtmasi bor."
        )
        return

    try:
        await context.bot.send_message(
            chat_id=int(ZILOLA_CHAT_ID),
            text=matn
        )

        await update.message.reply_text(
            "Zilolaga yuborildi ✅"
        )

    except Exception as e:
        print("ZILOLA XATOLIK:", e)

        await update.message.reply_text(
            "Zilolaga xabar yuborishda xatolik bo'ldi."
        )


async def ai_javob_ol(chat_id, savol):
    save_chat(chat_id, "user", savol)

    tarix = tarix_matni(chat_id)
    employee = get_employee_by_chat_id(chat_id)

    employee_info = "Foydalanuvchi bazada aniqlanmagan."

    if employee:
        employee_info = f"""
Foydalanuvchi:
Ism: {employee['name']}
Lavozim: {employee['role']}
Vazifalar: {employee['duties']}
Izoh: {employee['notes']}
"""

    prompt = f"""
{SORO_CONTEXT}

Hozirgi O'zbekiston sana va vaqti:
{hozirgi_vaqt()}

{employee_info}

Oldingi suhbat:
{tarix}

Foydalanuvchining oxirgi xabariga javob ber.
"""

    response = await asyncio.to_thread(
        client.responses.create,
        model="gpt-5.6",
        input=prompt,
    )

    javob = response.output_text.strip()

    save_chat(chat_id, "assistant", javob)

    return javob


class QarzXabariNatija(BaseModel):
    qarz_xabarimi: bool
    dokon_nomi: Optional[str] = None
    harakat: Optional[Literal["QARZ", "TULOV"]] = None
    summa: Optional[int] = None
    qoldiq: Optional[int] = None


QARZ_AI_SYSTEM = """
Sen SORO pekarniyasining qarzdorlik guruhidagi xabarlarni tahlil qilasan.

Biznes modeli: SORO do'konlarga mahsulot jo'natadi, do'konlar shu mahsulot uchun
SORO'ga qarzdor bo'ladi (aksincha emas — SORO hech kimdan qarzga tovar OLMAYDI,
faqat BERADI). Harakatni MA'NOSIGA qarab aniqla, so'zlarga emas:
- Do'konga mahsulot/pul BERILGANI (yukladik, jo'natdik, qarzga berdik, qarzdor
  qildik) -> harakat="QARZ" (do'konning SORO'ga qarzi OSHADI).
- Do'kondan pul/to'lov QAYTARIB OLINGANI (oldim, qaytardi, to'ladi, to'lov qildi) ->
  harakat="TULOV" (do'konning qarzi KAMAYADI). MUHIM: "dan ... oldim" — "do'kondan
  pul oldim" — bu SORO pul olgani, ya'ni do'kon TO'LAGANI, shuning uchun har doim
  TULOV, hech qachon QARZ emas — garchi xabarda "qarz" so'zi bo'lmasa ham.

Misollar:
- "Chinor 2 dan 452000 sum qarz berdik" -> QARZ, dokon_nomi="Chinor 2", summa=452000
- "Chinor 2 dan 200000 sum oldim" -> TULOV, dokon_nomi="Chinor 2", summa=200000
- "Bek market ga 300000 sum yukladik" -> QARZ, dokon_nomi="Bek market", summa=300000
- "Bek market to'ladi 150000" -> TULOV, dokon_nomi="Bek market", summa=150000

Do'kon nomini xabardan ajratib olishda faqat pul summasi, "qarz", "oldim", "dan",
"ga", "sum", "сум" so'zlarini olib tashla — nomning o'zidagi raqam yoki tartib
sonini (masalan "Chinor 2", "Do'kon №3") HECH QACHON kesib tashlama, u nomning
ajralmas qismi.

Xabarda "остатка"/"qoldiq"/"balans" so'zi bilan do'konning YAKUNIY qoldig'i
aytilgan bo'lsa, uni "qoldiq" maydoniga yoz (bu tranzaksiya summasiga qo'shilmaydi).

Agar xabarda FAQAT qoldiq/balans aytilgan bo'lsa (yangi tranzaksiya yo'q, masalan
"Chinor qoldig'i 100000" yoki "Chinor остатка 100000"), qarz_xabarimi=true,
harakat va summa bo'sh (null), qoldiq esa to'ldirilgan holda qaytar.

Xabar umuman qarz/to'lov/qoldiq haqida bo'lmasa (oddiy suhbat, savol va h.k.),
qarz_xabarimi=false qaytar, boshqa maydonlarni bo'sh qoldir.
Xabar rus, o'zbek (lotin/kiril) tillarida, imlo xatolari bilan bo'lishi mumkin.
"""


async def qarz_ai_tahlil(xabar):
    try:
        response = await asyncio.to_thread(
            client.responses.parse,
            model="gpt-4o-mini",
            input=[
                {"role": "system", "content": QARZ_AI_SYSTEM},
                {"role": "user", "content": xabar},
            ],
            text_format=QarzXabariNatija,
        )
        return response.output_parsed
    except Exception as e:
        print(f"QARZ AI TAHLIL XATOSI: {e}")
        return None


async def qarz_guruh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    if not DEBT_GROUP_CHAT_ID or str(chat.id) != str(DEBT_GROUP_CHAT_ID):
        return

    xabar = update.message.text
    kim = update.effective_user.first_name or "Noma'lum"
    print(f"QARZ GURUHI | {kim}: {xabar}")

    if not re.search(r"\d", xabar):
        return

    natija = await qarz_ai_tahlil(xabar)
    if natija is not None:
        if not natija.qarz_xabarimi or not natija.dokon_nomi:
            return
        magazin = natija.dokon_nomi

        if natija.harakat and natija.summa:
            add_debt(
                magazin, natija.summa, natija.harakat, xabar, kim,
                chat.id, update.message.message_id,
            )
            print(f"{natija.harakat} SAQLANDI (AI) | {magazin} | {natija.summa} | {kim}")

        if natija.qoldiq is not None:
            hozirgi_qoldiq = get_debt_balance(magazin, qarz_boshlanish_sanasi())
            farq = natija.qoldiq - hozirgi_qoldiq
            if farq > 0:
                add_debt(magazin, farq, "QARZ", f"AUTO QOLDIQ TUZATISH | {xabar}", kim)
            elif farq < 0:
                add_debt(magazin, abs(farq), "TULOV", f"AUTO QOLDIQ TUZATISH | {xabar}", kim)
            print(f"QOLDIQ TENGLANDI (AI) | {magazin} | {natija.qoldiq} | {kim}")
        return

    # AI mavjud bo'lmasa (masalan tarmoq xatosi), eski kalit-so'z asosidagi
    # tahlilga qaytamiz — qarz kuzatuvi hech qachon butunlay to'xtamasligi kerak.
    await qarz_guruh_regex(update, context, xabar, kim, chat)


async def qarz_guruh_regex(update: Update, context: ContextTypes.DEFAULT_TYPE, xabar, kim, chat):
    past = xabar.lower().replace("’", "'").replace("‘", "'")
    for eski, yangi in (("қарз", "qarz"), ("карз", "qarz"), ("қариз", "qarz"), ("кариз", "qarz"), ("олдим", "oldim")):
        past = past.replace(eski, yangi)

    # Pul summalarini magazin nomidagi oddiy raqamlardan ajratamiz.
    # Masalan: "Чинор 2 дан 452.000 сум ..." -> magazin "Чинор 2", summa 452000.
    money_matches = list(re.finditer(
        r"(?<!\d)(?:\d{1,3}(?:[\s.,\u00a0]\d{3})+|\d{4,})(?!\d)", xabar
    ))
    if not money_matches:
        return

    # "Остатка/Qoldiq" bo'lsa, undan keyingi raqam qoldiq; tranzaksiya summasiga qo'shilmaydi.
    lower_xabar = xabar.lower()
    ostatka_pos = len(xabar)
    for kalit in ("остатка", "ostatka", "qoldiq", "қолдиқ"):
        p = lower_xabar.find(kalit)
        if p != -1:
            ostatka_pos = min(ostatka_pos, p)

    tranzaksiya_matches = [m for m in money_matches if m.start() < ostatka_pos]
    if not tranzaksiya_matches:
        tranzaksiya_matches = [money_matches[0]]

    try:
        summalar = [int(re.sub(r"\D", "", m.group())) for m in tranzaksiya_matches]
    except ValueError:
        return

    # 1410.000+185.000+747.000 kabi yozuvlar yig'indisi olinadi.
    summa = sum(summalar)

    def magazin_nomi(matn):
        # Magazin nomi birinchi pul summasidan oldingi qismdan olinadi.
        # Shuning uchun "Чинор 2" dagi 2 yo'qolmaydi.
        birinchi_pul = money_matches[0]
        nom = matn[:birinchi_pul.start()]
        nom = re.sub(r"\b(qarz\w*|карз\w*|қарз\w*|кариз\w*|қариз\w*|oldim|олдим|to\'ladi|toladi|сум|сўм|sum)\b", " ", nom, flags=re.IGNORECASE)
        nom = re.sub(r"\b(dan|дан)\b", " ", nom, flags=re.IGNORECASE)
        nom = re.sub(r"\s+", " ", nom).strip(" .,-")
        return nom or "Noma\'lum"

    magazin = magazin_nomi(xabar)

    if "oldim" in past or "to'ladi" in past or "toladi" in past:
        add_debt(
            magazin, summa, "TULOV", xabar, kim,
            chat.id, update.message.message_id,
        )
        print(f"TULOV SAQLANDI | {magazin} | {summa} | {kim}")
    elif "qarz" in past:
        add_debt(
            magazin, summa, "QARZ", xabar, kim,
            chat.id, update.message.message_id,
        )
        print(f"QARZ SAQLANDI | {magazin} | {summa} | {kim}")
    else:
        return

    # Agar xabarda "Остатка / Qoldiq" yozilgan bo'lsa,
    # JARVIS shu magazinning yakuniy qarzini aynan o'sha summaga tenglaydi.
    if ostatka_pos < len(xabar):
        qoldiq_match = next((m for m in money_matches if m.start() > ostatka_pos), None)
        if qoldiq_match:
            try:
                qoldiq = int(re.sub(r"\D", "", qoldiq_match.group()))

                import sqlite3
                con = sqlite3.connect(BASE_DIR / "jarvis.db")
                cur = con.cursor()
                cur.execute("""
                    SELECT COALESCE(SUM(
                        CASE
                            WHEN action = 'QARZ' THEN amount
                            WHEN action = 'TULOV' THEN -amount
                            ELSE 0
                        END
                    ), 0)
                    FROM debts
                    WHERE shop_name = ? AND COALESCE(employee_name, '') = ?
                """, (magazin, kim))
                hozirgi_qoldiq = int(cur.fetchone()[0] or 0)
                con.close()

                farq = qoldiq - hozirgi_qoldiq

                if farq > 0:
                    add_debt(
                        magazin, farq, "QARZ",
                        f"AUTO QOLDIQ TUZATISH | {xabar}", kim
                    )
                elif farq < 0:
                    add_debt(
                        magazin, abs(farq), "TULOV",
                        f"AUTO QOLDIQ TUZATISH | {xabar}", kim
                    )

                print(
                    f"QOLDIQ TENGLANDI | {magazin} | "
                    f"{qoldiq} | {kim}"
                )
            except Exception as e:
                print("QOLDIQNI TENGLASH XATOSI:", e)


QY_SHOP, QY_NEW_SHOP, QY_ACTION, QY_AMOUNT = range(4)


def qarz_shop_keyboard(shops):
    buttons = [
        [InlineKeyboardButton(shop, callback_data=f"qzs:{i}")]
        for i, shop in enumerate(shops)
    ]
    buttons.append([InlineKeyboardButton("➕ Yangi do'kon", callback_data="qznew")])
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="qzcancel")])
    return InlineKeyboardMarkup(buttons)


def qarz_action_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Do'kon qarzga oldi", callback_data="qzt:QARZ")],
        [InlineKeyboardButton("📤 Do'kon to'lov qildi", callback_data="qzt:TULOV")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="qzcancel")],
    ])


def qarz_holat_matni(magazin):
    qoldiq = get_debt_balance(magazin, qarz_boshlanish_sanasi())
    if qoldiq > 0:
        return f"💵 Hozirgi qarz holati: {qoldiq:,} so‘m (do'kon qarzdor)".replace(",", " ")
    if qoldiq < 0:
        return f"💵 Hozirgi qarz holati: {abs(qoldiq):,} so‘m (do'kon ortiqcha to'lagan)".replace(",", " ")
    return "💵 Hozirgi qarz holati: 0 so‘m"


async def qarz_yoz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not DEBT_GROUP_CHAT_ID or str(update.effective_chat.id) != str(DEBT_GROUP_CHAT_ID):
        await update.message.reply_text("Bu buyruq faqat qarz guruhida ishlaydi.")
        return ConversationHandler.END
    shops = get_debt_shops()
    context.user_data["qarz_shops"] = shops
    await update.message.reply_text(
        "🏪 Qaysi do'kon?\n\nRo'yxatdan tanlang yoki nomini (bosh harflarini ham) yozib qidiring:",
        reply_markup=qarz_shop_keyboard(shops),
    )
    return QY_SHOP


async def qarz_yoz_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "qzcancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        return ConversationHandler.END
    if query.data == "qznew":
        await query.edit_message_text("✍️ Do'kon nomini yozing:")
        return QY_NEW_SHOP
    index = int(query.data.split(":")[1])
    shops = context.user_data.get("qarz_shops", [])
    if index >= len(shops):
        await query.edit_message_text("❌ Do'kon topilmadi, qaytadan /qarz_yoz yozing.")
        return ConversationHandler.END
    magazin = shops[index]
    context.user_data["qarz_shop_name"] = magazin
    await query.edit_message_text(
        f"🏪 Do'kon: {magazin}\n{qarz_holat_matni(magazin)}\n\nQarz yoki to'lov?",
        reply_markup=qarz_action_keyboard(),
    )
    return QY_ACTION


async def qarz_yoz_shop_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    matn = update.message.text.strip()
    if not matn:
        return QY_SHOP
    if matn.casefold() in ("yangi", "янги"):
        await update.message.reply_text("✍️ Do'kon nomini yozing:")
        return QY_NEW_SHOP

    barcha = context.user_data.get("qarz_shops") or get_debt_shops()
    qidiruv_cf = matn.casefold()
    mos = [d for d in barcha if qidiruv_cf in d.casefold()]

    if len(mos) == 1:
        magazin = mos[0]
        context.user_data["qarz_shop_name"] = magazin
        await update.message.reply_text(
            f"🏪 Do'kon: {magazin}\n{qarz_holat_matni(magazin)}\n\nQarz yoki to'lov?",
            reply_markup=qarz_action_keyboard(),
        )
        return QY_ACTION

    if not mos:
        await update.message.reply_text(
            f"❌ \"{matn}\" bo'yicha do'kon topilmadi.\n"
            "Boshqa nom bilan qidiring, yoki yangi do'kon uchun «yangi» deb yozing."
        )
        return QY_SHOP

    context.user_data["qarz_shops"] = mos
    await update.message.reply_text(
        f"🔎 \"{matn}\" bo'yicha {len(mos)} ta do'kon topildi:",
        reply_markup=qarz_shop_keyboard(mos),
    )
    return QY_SHOP


async def qarz_yoz_new_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nom = update.message.text.strip()
    if not nom:
        await update.message.reply_text("✍️ Do'kon nomini yozing:")
        return QY_NEW_SHOP
    context.user_data["qarz_shop_name"] = nom
    await update.message.reply_text(
        f"🏪 Do'kon: {nom}\n{qarz_holat_matni(nom)}\n\nQarz yoki to'lov?",
        reply_markup=qarz_action_keyboard(),
    )
    return QY_ACTION


async def qarz_yoz_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "qzcancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        return ConversationHandler.END
    harakat = query.data.split(":")[1]
    context.user_data["qarz_action"] = harakat
    matn = "Necha so'm qarz berdingiz?" if harakat == "QARZ" else "Necha so'm to'lov oldingiz?"
    await query.edit_message_text(f"💵 {matn}")
    return QY_AMOUNT


async def qarz_yoz_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        summa = int(clean_number(update.message.text))
    except ValueError:
        await update.message.reply_text("❌ Summani raqamda yozing. Masalan: 500000")
        return QY_AMOUNT
    if summa <= 0:
        await update.message.reply_text("❌ Summa noldan katta bo'lishi kerak.")
        return QY_AMOUNT

    magazin = context.user_data.get("qarz_shop_name")
    harakat = context.user_data.get("qarz_action")
    kim = update.effective_user.first_name or "Noma'lum"
    add_debt(
        magazin, summa, harakat, "qo'lda /qarz_yoz orqali kiritildi", kim,
        update.effective_chat.id, update.message.message_id,
    )

    qoldiq = get_debt_balance(magazin, qarz_boshlanish_sanasi())
    harakat_matni = "Qarz qo'shildi" if harakat == "QARZ" else "To'lov qayd etildi"
    await update.message.reply_text(
        f"✅ {harakat_matni}: {magazin} — {summa:,} so‘m".replace(",", " ")
        + f"\n💰 Joriy qoldiq: {qoldiq:,} so‘m".replace(",", " ")
    )
    context.user_data.pop("qarz_shops", None)
    context.user_data.pop("qarz_shop_name", None)
    context.user_data.pop("qarz_action", None)
    return ConversationHandler.END


async def qarz_yoz_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Bekor qilindi.")
    return ConversationHandler.END


def qarz_yoz_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("qarz_yoz", qarz_yoz_start)],
        states={
            QY_SHOP: [
                CallbackQueryHandler(qarz_yoz_shop, pattern=r"^(qzs:\d+|qznew|qzcancel)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, qarz_yoz_shop_search),
            ],
            QY_NEW_SHOP: [MessageHandler(filters.TEXT & ~filters.COMMAND, qarz_yoz_new_shop)],
            QY_ACTION: [CallbackQueryHandler(qarz_yoz_action, pattern=r"^(qzt:QARZ|qzt:TULOV|qzcancel)$")],
            QY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, qarz_yoz_amount)],
        },
        fallbacks=[
            CommandHandler("bekor", qarz_yoz_cancel),
            # Eski suhbatda "qotib qolgan" bo'lsa ham /qarz_yoz har doim
            # yangidan boshlay olishi uchun.
            CommandHandler("qarz_yoz", qarz_yoz_start),
        ],
    )


XOMASHYO_MONEY_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:[\s., ]\d{3})+|\d{4,})(?!\d)")
XOMASHYO_QTY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(кг|kg|та|dona|шт|litr|л|qop|қоп|karobka|коробка|konteyner|kanister)\b",
    re.IGNORECASE,
)
# "16 09 2026 бозорлик" kabi sana bilan boshlangan qatorlarda sananing bir
# qismi (masalan yil) narx sifatida noto'g'ri o'qilib qolmasligi uchun.
XOMASHYO_DATE_PREFIX_RE = re.compile(r"^\d{1,2}[.\s]\d{1,2}(?:[.\s]\d{2,4})?\s+")
XOMASHYO_BOZORLIK_SOZLAR = ("бозорлик", "bozorlik")
XOMASHYO_OSTATKA_SOZLAR = ("остатка", "остадка", "ostatka", "қолдиқ", "qoldiq")
# Haydovchilarga tegishli pul yozuvlari (masalan "Равшан 4 277 000") xomashyo
# xaridi emas, shuning uchun DRIVERS ro'yxatidagi ismlarning kirillcha
# ildizlari bilan boshlangan qatorlar alohida "boshqa" toifasiga ajratiladi.
XOMASHYO_HAYDOVCHI_ILDIZLARI = (
    "равшан", "баходир", "бахадир", "редван", "асад", "фаррух", "фаррох", "фарид",
)


def xomashyo_kategoriyasi(matn):
    """Qator xarid (mahsulot), bozorlik (bozorga berilgan pul), ostatka (qaytgan pul)
    yoki boshqa (haydovchi/shaxsiy to'lov) turlaridan qaysi biriga tegishli."""
    past = matn.lower().strip()
    if any(soz in past for soz in XOMASHYO_OSTATKA_SOZLAR):
        return "ostatka"
    if any(soz in past for soz in XOMASHYO_BOZORLIK_SOZLAR):
        return "bozorlik"
    birinchi_soz = past.split(" ", 1)[0] if past else ""
    if any(birinchi_soz.startswith(ildiz) for ildiz in XOMASHYO_HAYDOVCHI_ILDIZLARI):
        return "boshqa"
    return "xarid"


def parse_xomashyo_line(line):
    """Erkin matn qatoridan xomashyo nomi, miqdori, narxi va turini ajratib olishga harakat qiladi."""
    line = line.strip()
    if not line:
        return None

    kategoriya = xomashyo_kategoriyasi(line)
    text = line
    if kategoriya != "xarid":
        sanasiz = XOMASHYO_DATE_PREFIX_RE.sub("", line, count=1).strip()
        if sanasiz:
            text = sanasiz

    price = None
    money_matches = list(XOMASHYO_MONEY_RE.finditer(text))
    if money_matches:
        last = money_matches[-1]
        price = int(re.sub(r"\D", "", last.group()))
        text = (text[:last.start()] + text[last.end():]).strip()

    quantity = unit = None
    qty_match = XOMASHYO_QTY_RE.search(text)
    if qty_match:
        remaining = (text[:qty_match.start()] + text[qty_match.end():]).strip(" -–:")
        if remaining:
            quantity = float(qty_match.group(1).replace(",", "."))
            unit = qty_match.group(2).lower()
            text = remaining

    item_name = text.strip(" -–:") or line
    return {
        "item_name": item_name, "quantity": quantity, "unit": unit,
        "price": price, "category": kategoriya,
    }


XOMASHYO_SAVOL_SOZLAR = (
    "qancha", "канча", "неча", "нечта", "nechta", "nechi", "necha",
    "qanaqa", "қанақа", "qanday", "қандай", "hisobot", "ҳисобот",
)


def xomashyo_savolmi(line):
    """Qator xomashyo yozuvimi yoki savolmi (masalan "nechi xil xomashyo bor?")."""
    if XOMASHYO_MONEY_RE.search(line):
        return False
    past = line.lower()
    return "?" in past or any(soz in past for soz in XOMASHYO_SAVOL_SOZLAR)


async def xomashyo_savolga_javob(update: Update, context: ContextTypes.DEFAULT_TYPE, savol):
    with delivery_db() as con:
        nomlar = [
            row["item_name"] for row in
            con.execute("SELECT DISTINCT item_name FROM xomashyo_log WHERE category='xarid'").fetchall()
        ]
    savol_cf = savol.casefold()
    nomzodlar = [nom for nom in nomlar if len(nom) >= 3 and nom.casefold() in savol_cf]
    mos_nom = max(nomzodlar, key=len) if nomzodlar else None

    if mos_nom:
        # Guruhda faqat so'ralgan mahsulot haqida qisqa javob beramiz.
        rows = xomashyo_summary(qidiruv=mos_nom)
        text = format_xomashyo_report(None, None, rows, mos_nom)
        await update.message.reply_text(text)
        return

    # Mahsulot aniq bo'lmasa, to'liq moliyaviy hisobot guruhga emas,
    # faqat adminlarga shaxsiy yuboriladi.
    await update.message.reply_text(
        "Bismillahir rohmanir rohim\n\nTo'liq hisobotni administratorga yubordim."
    )
    rows = xomashyo_summary()
    bozorlik = xomashyo_kategoriya_kim(None, None, "bozorlik")
    ostatka = xomashyo_kategoriya_kim(None, None, "ostatka")
    boshqa = xomashyo_kategoriya_kim(None, None, "boshqa")
    text = format_xomashyo_report(
        None, None, rows, bozorlik_rows=bozorlik, ostatka_rows=ostatka, boshqa_rows=boshqa
    )
    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(chat_id=int(admin_id), text=text)
        except Exception as e:
            print(f"SAVOLGA JAVOB YUBORISH XATOSI | {admin_id} | {e}")


async def bozor_guruh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat = update.effective_chat
    if not BOZOR_GROUP_CHAT_ID or str(chat.id) != str(BOZOR_GROUP_CHAT_ID):
        return

    kim = update.effective_user.first_name or "Noma'lum"
    work_date = update.message.date.astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%d")

    rows = []
    savol_qatori = None
    for line in update.message.text.splitlines():
        line = line.strip()
        if not line:
            continue
        if xomashyo_savolmi(line):
            savol_qatori = savol_qatori or line
            continue
        parsed = parse_xomashyo_line(line)
        if not parsed:
            continue
        rows.append((
            str(chat.id), update.message.message_id, kim, line,
            parsed["item_name"], parsed["quantity"], parsed["unit"], parsed["price"],
            work_date, parsed["category"],
        ))

    if rows:
        with delivery_db() as con:
            con.executemany("""
                INSERT INTO xomashyo_log(
                    telegram_chat_id, message_id, sender_name, raw_text,
                    item_name, quantity, unit, price, work_date, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
        print(f"XOMASHYO QAYD ETILDI | {kim} | {len(rows)} qator")

    if savol_qatori:
        await xomashyo_savolga_javob(update, context, savol_qatori)


XOMASHYO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def xomashyo_kategoriya_kim(boshlanish, tugash, kategoriya):
    """Bozorlik/ostatka summalarini kim yozganiga qarab bo'lib beradi (kim bozorga borgani)."""
    query = (
        "SELECT sender_name, COALESCE(SUM(price), 0) AS jami FROM xomashyo_log "
        "WHERE category = ?"
    )
    params = [kategoriya]
    if boshlanish:
        query += " AND work_date >= ?"
        params.append(boshlanish)
    if tugash:
        query += " AND work_date <= ?"
        params.append(tugash)
    query += " GROUP BY sender_name COLLATE NOCASE HAVING jami > 0 ORDER BY jami DESC"
    with delivery_db() as con:
        return con.execute(query, params).fetchall()


def _xomashyo_oxshashmi(a, b):
    # Kirill matnlarda tez-tez uchraydigan bitta-ikkita harf xatosini
    # (masalan "Молоко"/"Малоко") qo'lda ro'yxat tuzmasdan avtomatik aniqlash.
    if min(len(a), len(b)) < 4:
        return a == b
    return SequenceMatcher(None, a, b).ratio() >= 0.82


def _xomashyo_kanonik_guruhlar(groups):
    """Yozilishi bir-biriga juda yaqin nomlarni (imlo xatosi) bitta guruhga birlashtiradi."""
    kalitlar = list(groups.keys())
    ota = {kalit: kalit for kalit in kalitlar}

    def topish(kalit):
        while ota[kalit] != kalit:
            ota[kalit] = ota[ota[kalit]]
            kalit = ota[kalit]
        return kalit

    for i, a in enumerate(kalitlar):
        for b in kalitlar[i + 1:]:
            if a[1] != b[1]:  # birlik bir xil bo'lishi shart
                continue
            if _xomashyo_oxshashmi(a[0], b[0]):
                ota[topish(a)] = topish(b)

    birlashgan = {}
    for kalit in kalitlar:
        bosh = topish(kalit)
        asosiy = birlashgan.setdefault(bosh, {
            "item_name": groups[kalit]["item_name"], "unit": groups[kalit]["unit"],
            "total_qty": None, "total_price": None, "cnt": 0,
        })
        g = groups[kalit]
        # Eng ko'p uchragan yozilishini asosiy nom sifatida ko'rsatamiz.
        if g["cnt"] > asosiy["cnt"]:
            asosiy["item_name"] = g["item_name"]
        asosiy["cnt"] += g["cnt"]
        if g["total_qty"] is not None:
            asosiy["total_qty"] = (asosiy["total_qty"] or 0) + g["total_qty"]
        if g["total_price"] is not None:
            asosiy["total_price"] = (asosiy["total_price"] or 0) + g["total_price"]
    return list(birlashgan.values())


def xomashyo_summary(boshlanish=None, tugash=None, qidiruv=None):
    # Guruhlash va qidiruv Python tomonida amalga oshiriladi, chunki SQLite'ning
    # NOCASE kollatsiyasi faqat ASCII harflarni farqlaydi, kirill harflarini emas.
    # "bozorlik" (bozorga berilgan pul) va "ostatka" (qaytgan pul) alohida
    # hisoblanadi, shuning uchun bu yerda faqat haqiqiy xaridlar olinadi.
    query = "SELECT item_name, unit, quantity, price FROM xomashyo_log WHERE category='xarid'"
    params = []
    if boshlanish:
        query += " AND work_date >= ?"
        params.append(boshlanish)
    if tugash:
        query += " AND work_date <= ?"
        params.append(tugash)
    query += " ORDER BY id"
    with delivery_db() as con:
        rows = con.execute(query, params).fetchall()

    qidiruv_cf = qidiruv.casefold() if qidiruv else None
    groups = {}
    for row in rows:
        if qidiruv_cf and qidiruv_cf not in row["item_name"].casefold():
            continue
        key = (row["item_name"].casefold(), row["unit"])
        group = groups.setdefault(key, {
            "item_name": row["item_name"], "unit": row["unit"],
            "total_qty": None, "total_price": None, "cnt": 0,
        })
        group["cnt"] += 1
        if row["quantity"] is not None:
            group["total_qty"] = (group["total_qty"] or 0) + row["quantity"]
        if row["price"] is not None:
            group["total_price"] = (group["total_price"] or 0) + row["price"]

    groups = {
        (g["item_name"].casefold(), g["unit"]): g
        for g in _xomashyo_kanonik_guruhlar(groups)
    }

    return sorted(
        groups.values(),
        key=lambda g: (g["total_price"] is None, -(g["total_price"] or 0), g["item_name"]),
    )


def _xomashyo_kim_qismi(sarlavha, sozlar):
    jami = sum(row["jami"] for row in sozlar)
    text = f"\n{sarlavha}: {jami:,} so‘m".replace(",", " ")
    if len(sozlar) > 1:
        for row in sozlar:
            kim = row["sender_name"] or "Noma'lum"
            text += f"\n   • {kim} — {row['jami']:,} so‘m".replace(",", " ")
    elif len(sozlar) == 1 and sozlar[0]["sender_name"]:
        text += f" ({sozlar[0]['sender_name']})"
    return text


def format_xomashyo_report(
    boshlanish, tugash, rows, qidiruv=None,
    bozorlik_rows=None, ostatka_rows=None, boshqa_rows=None,
):
    if boshlanish is None and tugash is None:
        sana_qismi = "barcha vaqt"
    elif boshlanish == tugash:
        sana_qismi = boshlanish
    else:
        sana_qismi = f"{boshlanish} — {tugash}"
    sarlavha = f"Bismillahir rohmanir rohim\n\n📦 XOMASHYO HISOBOTI — {sana_qismi}"
    if qidiruv:
        sarlavha += f" (qidiruv: {qidiruv})"

    text = sarlavha + "\n\n"
    jami = 0
    if not rows:
        text += "Mahsulot yozuvi topilmadi.\n"
    for row in rows:
        qty_part = f" — jami {fmt_qty(row['total_qty'])} {row['unit']}" if row["total_qty"] else ""
        price_part = ""
        if row["total_price"]:
            jami += row["total_price"]
            price_part = f" — {row['total_price']:,} so‘m".replace(",", " ")
        marta = f" ({row['cnt']} marta)" if row["cnt"] > 1 else ""
        text += f"• {row['item_name']}{qty_part}{price_part}{marta}\n"
    text += f"\n💵 Mahsulotlarga sarflangan: {jami:,} so‘m".replace(",", " ")
    if bozorlik_rows is not None:
        text += _xomashyo_kim_qismi("🛒 Bozorga olib ketilgan summa", bozorlik_rows)
    if ostatka_rows is not None:
        text += _xomashyo_kim_qismi("↩️ Ishlatilmay qaytgan (ostatka)", ostatka_rows)
    if boshqa_rows is not None and boshqa_rows:
        text += _xomashyo_kim_qismi("🧑‍🤝‍🧑 Boshqa (haydovchi/shaxsiy) to‘lovlar", boshqa_rows)
    return text


async def xomashyo_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sanalar = [arg for arg in context.args if XOMASHYO_DATE_RE.match(arg)]
    qidiruv_qismlari = [arg for arg in context.args if not XOMASHYO_DATE_RE.match(arg)]
    boshlanish = sanalar[0] if sanalar else uz_today()
    tugash = sanalar[1] if len(sanalar) > 1 else boshlanish
    qidiruv = " ".join(qidiruv_qismlari) if qidiruv_qismlari else None

    rows = xomashyo_summary(boshlanish, tugash, qidiruv)
    bozorlik = ostatka = boshqa = None
    if not qidiruv:
        bozorlik = xomashyo_kategoriya_kim(boshlanish, tugash, "bozorlik")
        ostatka = xomashyo_kategoriya_kim(boshlanish, tugash, "ostatka")
        boshqa = xomashyo_kategoriya_kim(boshlanish, tugash, "boshqa")
    text = format_xomashyo_report(boshlanish, tugash, rows, qidiruv, bozorlik, ostatka, boshqa)
    await send_long_message(update.message, text)


async def kunlik_bozor_yuborish(context: ContextTypes.DEFAULT_TYPE):
    sana = uz_today()
    rows = xomashyo_summary(sana, sana)
    bozorlik = xomashyo_kategoriya_kim(sana, sana, "bozorlik")
    ostatka = xomashyo_kategoriya_kim(sana, sana, "ostatka")
    boshqa = xomashyo_kategoriya_kim(sana, sana, "boshqa")
    text = format_xomashyo_report(
        sana, sana, rows, bozorlik_rows=bozorlik, ostatka_rows=ostatka, boshqa_rows=boshqa
    )
    for admin_id in ADMIN_USER_IDS:
        try:
            await context.bot.send_message(chat_id=int(admin_id), text=text)
        except Exception as e:
            print(f"KUNLIK BOZOR YUBORISH XATOSI | {admin_id} | {e}")


async def matn_javob(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message = update.effective_message
    savol = message.text
    past = savol.lower()

    # Telegram kanal postlari CommandHandler'ga tushmasa ham /chatid ishlaydi.
    if past.startswith("/chatid"):
        await message.reply_text(f"Ushbu chat yoki kanal ID raqami:\n{chat_id}")
        return

    # Zilolaga oddiy gap bilan xabar yuborish
    if (
        "zilolaga yoz" in past
        or "zilolaga yubor" in past
        or "zilolaga ayt" in past
    ):
        if not ZILOLA_CHAT_ID:
            await message.reply_text(
                "Zilolaning chat_id si topilmadi."
            )
            return

        matn = savol

        almashtirishlar = [
            "Jarvis,",
            "jarvis,",
            "Zilolaga yoz:",
            "zilolaga yoz:",
            "Zilolaga yubor:",
            "zilolaga yubor:",
            "Zilolaga ayt:",
            "zilolaga ayt:",
            "Zilolaga yoz",
            "zilolaga yoz",
            "Zilolaga yubor",
            "zilolaga yubor",
            "Zilolaga ayt",
            "zilolaga ayt",
        ]

        for qism in almashtirishlar:
            matn = matn.replace(qism, "")

        matn = matn.strip(" :,-")

        try:
            await context.bot.send_message(
                chat_id=int(ZILOLA_CHAT_ID),
                text=matn
            )

            await message.reply_text(
                "Zilolaga yuborildi ✅"
            )

        except Exception as e:
            print("ZILOLA XATOLIK:", e)

            await message.reply_text(
                "Zilolaga yuborishda xatolik bo'ldi."
            )

        return

    try:
        javob = await ai_javob_ol(
            chat_id,
            savol
        )

        await send_long_message(message, javob)

    except Exception as e:
        print("MATN XATOLIK:", e)

        await message.reply_text(
            "JARVISda xatolik yuz berdi."
        )


async def ovoz_javob(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    audio_path = None

    try:
        await update.message.reply_text(
            "🎙 Ovozli xabarni eshityapman..."
        )

        voice = update.message.voice

        telegram_file = await context.bot.get_file(
            voice.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".ogg",
            delete=False
        ) as temp_file:
            audio_path = temp_file.name

        await telegram_file.download_to_drive(
            custom_path=audio_path
        )

        with open(audio_path, "rb") as audio_file:
            transcript = await asyncio.to_thread(
                client.audio.transcriptions.create,
                model="gpt-4o-mini-transcribe",
                file=audio_file,
            )

        savol = transcript.text.strip()

        await update.message.reply_text(
            f"📝 Siz aytdingiz:\n{savol}"
        )

        javob = await ai_javob_ol(
            chat_id,
            savol
        )

        await send_long_message(update.message, javob)

    except Exception as e:
        print("OVOZ XATOLIK:", e)

        await update.message.reply_text(
            "Ovozli xabarni tushunishda xatolik bo'ldi."
        )

    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

async def zilola_hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = await asyncio.to_thread(get_zilola_report)
        await send_long_message(update.message, report)
    except Exception as e:
        await update.message.reply_text(f"❌ Zilola hisobotini olishda xato: {e}")
async def keldi_ketdi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = await asyncio.to_thread(get_keldi_ketdi)
        await send_long_message(update.message, report)
    except Exception as e:
        await update.message.reply_text(f"❌ Keldi-ketdini olishda xato: {e}")

async def davomat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = await asyncio.to_thread(get_davomat)
        await send_long_message(update.message, report)
    except Exception as e:
        await update.message.reply_text(f"❌ Davomatni olishda xato: {e}")
async def haftalik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = await asyncio.to_thread(get_haftalik)

        # Telegram bitta xabarda taxminan 4096 belgigacha qabul qiladi.
        # Hisobot uzun bo'lsa, xavfsiz ravishda bir nechta xabarga bo'lamiz.
        limit = 3800
        qismlar = []
        qolgan = report

        while len(qolgan) > limit:
            kesish = qolgan.rfind("\n", 0, limit)
            if kesish <= 0:
                kesish = limit
            qismlar.append(qolgan[:kesish])
            qolgan = qolgan[kesish:].lstrip("\n")

        if qolgan:
            qismlar.append(qolgan)

        for qism in qismlar:
            await update.message.reply_text(qism)

    except Exception as e:
        await update.message.reply_text(f"❌ Haftalik davomatni olishda xato: {e}")
async def oylik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        report = await asyncio.to_thread(get_oylik)
        await send_long_message(update.message, report)
    except Exception as e:
        await update.message.reply_text(f"❌ Oylik davomatni olishda xato: {e}")
QARZ_BOSHLANISH_KEY = "qarz_boshlanish_sanasi"


def qarz_boshlanish_sanasi():
    """Qarz hisobi qaysi sanadan boshlab yuritilishi (masalan eski, chalkash
    yozuvlarni e'tiborsiz qoldirish uchun). Sozlanmagan bo'lsa — butun tarix hisoblanadi."""
    return get_setting(QARZ_BOSHLANISH_KEY, "") or None


async def qarz_bosh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        hozirgi = qarz_boshlanish_sanasi()
        matn = hozirgi if hozirgi else "belgilanmagan (butun tarix hisoblanmoqda)"
        await update.message.reply_text(
            f"📅 Qarz hisobi boshlanish sanasi: {matn}\n\n"
            "O'zgartirish uchun: /qarz_bosh 2026-09-19\n"
            "Butun tarixni hisoblashga qaytarish uchun: /qarz_bosh hammasi"
        )
        return
    qiymat = context.args[0]
    if qiymat.lower() in ("hammasi", "barchasi", "off"):
        set_setting(QARZ_BOSHLANISH_KEY, "")
        await update.message.reply_text("✅ Qarz hisobi endi butun tarix bo'yicha yuritiladi.")
        return
    if not XOMASHYO_DATE_RE.match(qiymat):
        await update.message.reply_text("❌ Sanani YYYY-MM-DD ko'rinishida yozing. Masalan: 2026-09-19")
        return
    set_setting(QARZ_BOSHLANISH_KEY, qiymat)
    await update.message.reply_text(
        f"✅ Qarz hisobi endi {qiymat} sanasidan boshlab yuritiladi.\n"
        "Bu sanadan oldingi yozuvlar bazada saqlanadi, lekin hisobotga kirmaydi."
    )


async def qarz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        boshlanish = qarz_boshlanish_sanasi()
        sana = context.args[0] if context.args else uz_today()
        kunlik = get_debt_daily_summary(sana)
        rows = get_debt_summary(boshlanish)

        text = "Bismillahir rohmanir rohim\n\n💰 SORO — QARZDORLIKLAR\n"
        if boshlanish:
            text += f"(hisob {boshlanish} sanasidan boshlab yuritilmoqda)\n"
        text += "\n"
        text += f"📅 {sana}:\n"
        text += f"📥 Qarzga berildi: {kunlik['QARZ']:,} so‘m\n".replace(",", " ")
        text += f"📤 Qaytarildi (to‘lov): {kunlik['TULOV']:,} so‘m\n".replace(",", " ")
        text += "━━━━━━━━━━\n\n"
        jami = 0

        for shop_name, employee_name, balance in rows:
            if balance == 0:
                continue

            xodim = employee_name if employee_name else "Noma'lum"

            text += f"🏪 {shop_name}\n"
            text += f"👤 Dastavchik: {xodim}\n"
            text += f"💵 Qoldiq: {balance:,} so‘m\n\n".replace(",", " ")

            jami += balance

        text += "━━━━━━━━━━\n"
        text += f"💰 JAMI (barcha do‘konlar bizdan qarzdor): {jami:,} so‘m".replace(",", " ")

        await send_long_message(update.message, text)

    except Exception as e:

        await update.message.reply_text(f"❌ Qarz hisobini olishda xato: {e}")

def build_application():
    init_db()
    init_delivery_db()
    settings.validate()

    if not ADMIN_USER_IDS:
        print("OGOHLANTIRISH: ADMIN_USER_IDS bo'sh. Faqat bazada admin/owner/director/rahbar roli borlar admin bo'ladi.")

    app = Application.builder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("tozala", admin_only(tozala))
    )

    app.add_handler(
        CommandHandler("kimman", admin_only(kimman))
    )

    app.add_handler(
        CommandHandler("zilola", admin_only(zilolaga_yubor))
    )

    app.add_handler(CallbackQueryHandler(register_driver_choice, pattern=r"^regd:\d+$"))
    app.add_handler(CommandHandler("dastavchik_link", admin_only(dastavchik_link)))
    app.add_handler(CommandHandler("chatid", chat_id))
    app.add_handler(CommandHandler("yordam", yordam))
    app.add_handler(CommandHandler("yordam_belgila", admin_only(yordam_belgila)))

    # Dastavchik suhbatlari umumiy matn handleridan oldin turishi shart.
    yuklash_handler, qoldiq_handler = delivery_handlers()
    app.add_handler(yuklash_handler)
    app.add_handler(qoldiq_handler)
    app.add_handler(CommandHandler("dastavchik_hisobot", admin_only(delivery_report)))
    app.add_handler(CommandHandler("mening_hisobot", mening_hisobot))

    app.add_handler(
        MessageHandler(
            filters.VOICE,
            admin_only(ovoz_javob)
        )
    )
    if DEBT_GROUP_CHAT_ID:
        # Tugmali /qarz_yoz oqimi erkin matn handleridan OLDIN turishi shart,
        # aks holda suhbat davomidagi javoblarni qarz_guruh yutib oladi.
        app.add_handler(qarz_yoz_handler())
        # Guruhda haydovchilar va sotuvchilar ham qarz/to'lov yozadi,
        # shuning uchun admin_only bilan cheklanmaydi.
        app.add_handler(
            MessageHandler(
                filters.Chat(chat_id=int(DEBT_GROUP_CHAT_ID)) & filters.TEXT & ~filters.COMMAND,
                qarz_guruh,
            )
        )
    if BOZOR_GROUP_CHAT_ID:
        app.add_handler(
            MessageHandler(
                filters.Chat(chat_id=int(BOZOR_GROUP_CHAT_ID)) & filters.TEXT & ~filters.COMMAND,
                bozor_guruh,
            )
        )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            admin_only(matn_javob)
        )
    )
    app.add_handler(
        CommandHandler("zilolahisobot", admin_only(zilola_hisobot))
    )

    app.add_handler(
        CommandHandler("keldiketdi", admin_only(keldi_ketdi))
    )

    app.add_handler(
        CommandHandler("davomat", admin_only(davomat))
    )
    app.add_handler(
        CommandHandler("haftalik", admin_only(haftalik))
    )
    app.add_handler(
        CommandHandler("oylik", admin_only(oylik))
    )
    app.add_handler(
        CommandHandler("qarz", admin_only(qarz))
    )
    app.add_handler(
        CommandHandler("qarz_bosh", admin_only(qarz_bosh))
    )
    app.add_handler(
        CommandHandler("xomashyo_hisobot", admin_only(xomashyo_hisobot))
    )
    if BOZOR_GROUP_CHAT_ID and ADMIN_USER_IDS and app.job_queue:
        app.job_queue.run_daily(
            kunlik_bozor_yuborish,
            time=dt_time(hour=18, minute=0, tzinfo=timezone(timedelta(hours=5))),
            name="kunlik_bozor_yuborish",
        )
    return app


def main():
    app = build_application()
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, ConversationHandler):
                for entry in handler.entry_points:
                    if isinstance(entry, CommandHandler) and "yuklash" in entry.commands:
                        callback = entry.callback
                        print(
                            f"/yuklash ULANGAN HANDLER: {callback.__module__}.{callback.__name__} | "
                            f"{Path(callback.__code__.co_filename).resolve()}:{callback.__code__.co_firstlineno}",
                            flush=True,
                        )
    print(
        "SORO JARVIS ishga tushdi: "
        "ChatGPT + DOIMIY SQLite xotira + sana + chat_id + ovoz"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
