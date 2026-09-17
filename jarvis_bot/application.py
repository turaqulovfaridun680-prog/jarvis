import os
import asyncio
import math
import re
import sqlite3
import tempfile
import gspread
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from contextlib import contextmanager

from openai import OpenAI

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
)
from .config import ROOT_DIR, settings

BASE_DIR = ROOT_DIR
OPENAI_API_KEY = settings.openai_api_key
TELEGRAM_BOT_TOKEN = settings.telegram_bot_token
ZILOLA_CHAT_ID = settings.zilola_chat_id
DASTAVKA_REPORT_CHAT_ID = settings.delivery_report_chat_id
DEBT_GROUP_CHAT_ID = settings.debt_group_chat_id
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
        """)
        con.executemany(
            "INSERT OR IGNORE INTO delivery_drivers(name) VALUES (?)",
            [(name,) for name in DRIVERS],
        )
        con.executemany(
            "INSERT OR IGNORE INTO delivery_products(name) VALUES (?)",
            [(name,) for name in PRODUCTS],
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
                "/mening_hisobot — o‘z hisobotingiz"
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
            "/mening_hisobot — o‘z hisobotingiz"
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
async def qarz_guruh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat = update.effective_chat
    if not DEBT_GROUP_CHAT_ID or str(chat.id) != str(DEBT_GROUP_CHAT_ID):
        return

    xabar = update.message.text
    kim = update.effective_user.first_name or "Noma\'lum"
    print(f"QARZ GURUHI | {kim}: {xabar}")

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
async def qarz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rows = get_debt_summary()

        text = "💰 SORO — QARZDORLIKLAR\n\n"
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
        text += f"💰 JAMI: {jami:,} so‘m".replace(",", " ")

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
        app.add_handler(
            MessageHandler(
                filters.Chat(chat_id=int(DEBT_GROUP_CHAT_ID)) & filters.TEXT & ~filters.COMMAND,
                admin_only(qarz_guruh),
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
