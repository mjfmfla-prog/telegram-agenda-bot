import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG ================= #

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
NL_TZ = ZoneInfo("Europe/Amsterdam")
PORT = int(os.environ.get("PORT", 10000))

# ================= DB ================= #

conn = sqlite3.connect("agenda.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    title TEXT,
    event_time TEXT,
    reminded INTEGER DEFAULT 0
)
""")
conn.commit()

# ================= PARSER ================= #

def parse(text: str):
    text = text.lower().strip()
    now = datetime.now(NL_TZ)

    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5,
        "sunday": 6,
        "maandag": 0, "dinsdag": 1, "woensdag": 2,
        "donderdag": 3, "vrijdag": 4, "zaterdag": 5,
        "zondag": 6,
    }

    hour = None
    minute = 0

    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))

    if hour is None:
        match = re.search(r"\b(\d{1,2})\b", text)
        if match:
            hour = int(match.group(1))
            minute = 0

    if hour is None:
        return None, None

    event_date = None

    for d, idx in weekdays.items():
        if d in text:
            diff = idx - now.weekday()
            if diff <= 0:
                diff += 7
            event_date = (now + timedelta(days=diff)).date()
            break

    if event_date is None:
        if "tomorrow" in text or "morgen" in text:
            event_date = (now + timedelta(days=1)).date()
        elif "overmorgen" in text:
            event_date = (now + timedelta(days=2)).date()
        else:
            event_date = now.date()

    dt = datetime(event_date.year, event_date.month, event_date.day, hour, minute, tzinfo=NL_TZ)

    title = text
    for d in weekdays.keys():
        title = title.replace(d, "")

    title = re.sub(r"\d{1,2}:\d{2}", "", title)
    title = re.sub(r"\b\d{1,2}\b", "", title)

    for w in ["tomorrow", "morgen", "overmorgen"]:
        title = title.replace(w, "")

    title = title.strip()
    if title == "":
        title = "event"

    return title, dt

# ================= DB ================= #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()

def get_events(chat_id):
    cursor.execute("SELECT * FROM events WHERE chat_id=? ORDER BY event_time", (chat_id,))
    return cursor.fetchall()

def delete_event(event_id):
    cursor.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()

# ================= UI ================= #

def agenda_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Day", callback_data="day")],
        [InlineKeyboardButton("📆 Week", callback_data="week")],
        [InlineKeyboardButton("🗓 Month", callback_data="month")],
    ])

def event_keyboard(event_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Delete", callback_data=f"del_{event_id}")]
    ])

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Calendar bot active")

async def agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Choose view:", reply_markup=agenda_menu())

# ================= CALLBACKS ================= #

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat.id
    now = datetime.now(NL_TZ)

    data = q.data

    # DELETE EVENT
    if data.startswith("del_"):
        event_id = int(data.split("_")[1])
        delete_event(event_id)
        await q.message.reply_text("🗑 Event deleted")
        return

    events = get_events(chat_id)

    if data == "day":
        filtered = [e for e in events if datetime.fromisoformat(e[3]).date() == now.date()]
        title = "📅 Today"

    elif data == "week":
        filtered = [e for e in events if now.date() <= datetime.fromisoformat(e[3]).date() <= now.date() + timedelta(days=7)]
        title = "📆 This week"

    else:
        filtered = [e for e in events if datetime.fromisoformat(e[3]).month == now.month]
        title = "🗓 This month"

    if not filtered:
        await q.message.reply_text(f"{title}\n\nNo events")
        return

    msg = f"{title}\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])

        msg += (
            f"📌 {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"
            f"🆔 /delete {e[0]}\n\n"
        )

    await q.message.reply_text(msg)

# ================= MESSAGE ================= #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    title, dt = parse(text)

    if not dt:
        await update.message.reply_text("❌ Example: Sunday 19:30 dinner")
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Added\n📌 {title}\n🕒 {dt.strftime('%A %d %B %H:%M')}",
        reply_markup=event_keyboard(1)
    )

# ================= REMINDERS ================= #

def reminder_loop(app):
    while True:
        now = datetime.now(NL_TZ)

        cursor.execute("SELECT * FROM events")
        events = cursor.fetchall()

        for e in events:
            if e[4]:
                continue

            dt = datetime.fromisoformat(e[3])

            if now >= dt - timedelta(minutes=15):
                try:
                    app.bot.send_message(
                        chat_id=e[1],
                        text=f"🔔 Reminder\n📌
