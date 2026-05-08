import os
import re
import sqlite3
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG ================= #

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.environ.get("PORT", 10000))
NL_TZ = ZoneInfo("Europe/Amsterdam")

# ================= DB ================= #

conn = sqlite3.connect("agenda.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    title TEXT,
    event_time TEXT,
    reminded INTEGER DEFAULT 0,
    morning_sent INTEGER DEFAULT 0
)
""")
conn.commit()

# ================= PARSER ================= #

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5,
    "sunday": 6,
    "maandag": 0, "dinsdag": 1, "woensdag": 2,
    "donderdag": 3, "vrijdag": 4, "zaterdag": 5,
    "zondag": 6,
}

def parse(text: str):
    raw = text.lower().strip()
    now = datetime.now(NL_TZ)

    match = re.search(r"(\d{1,2}):(\d{2})", raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
    else:
        match = re.search(r"\b(\d{1,2})\b", raw)
        if match:
            hour = int(match.group(1))
            minute = 0
        else:
            return None, None

    event_date = None

    for d, idx in WEEKDAYS.items():
        if d in raw:
            diff = idx - now.weekday()
            if diff <= 0:
                diff += 7
            event_date = (now + timedelta(days=diff)).date()
            break

    if event_date is None:
        if "tomorrow" in raw or "morgen" in raw:
            event_date = (now + timedelta(days=1)).date()
        elif "overmorgen" in raw:
            event_date = (now + timedelta(days=2)).date()
        else:
            event_date = now.date()

    dt = datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        hour,
        minute,
        tzinfo=NL_TZ
    )

    # CLEAN TITLE
    title = raw

    for d in WEEKDAYS.keys():
        title = title.replace(d, "")

    title = re.sub(r"\d{1,2}:\d{2}", "", title)
    title = re.sub(r"\b\d{1,2}\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()

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

def get_all_chats():
    cursor.execute("SELECT DISTINCT chat_id FROM events")
    return cursor.fetchall()

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Calendar bot running")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    title, dt = parse(text)

    if not dt:
        await update.message.reply_text("❌ Example: Sunday 19:30 dinner")
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        "✅ Added\n📌 " + title +
        "\n🕒 " + dt.strftime("%A %d %B %H:%M")
    )

# ================= VIEWS ================= #

async def day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show(update, "day")

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show(update, "week")

async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show(update, "month")

async def show(update, mode):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = get_events(chat_id)

    if mode == "day":
        filtered = [e for e in events if datetime.fromisoformat(e[3]).date() == now.date()]
        title = "📅 Today"

    elif mode == "week":
        filtered = [
            e for e in events
            if now.date() <= datetime.fromisoformat(e[3]).date() <= now.date() + timedelta(days=7)
        ]
        title = "📆 This week"

    else:
        filtered = [e for e in events if datetime.fromisoformat(e[3]).month == now.month]
        title = "🗓 This month"

    if not filtered:
        await update.message.reply_text(title + "\n\nNo events")
        return

    msg = title + "\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        msg += "📌 " + e[2] + " → " + dt.strftime("%d-%m %H:%M") + "\n"

    await update.message.reply_text(msg)

# ================= MORNING SUMMARY ================= #

async def morning_summary(app):
    while True:
        now = datetime.now(NL_TZ)

        if now.hour == 8 and now.minute == 0:
            chats = get_all_chats()

            for c in chats:
                chat_id = c[0]

                cursor.execute(
                    "SELECT * FROM events WHERE chat_id=?",
                    (chat_id,)
                )
                events = cursor.fetchall()

                today = [
                    e for e in events
                    if datetime.fromisoformat(e[3]).date() == now.date()
                ]

                if today:
                    msg = "🌅 Today’s schedule:\n\n"

                    for e in today:
                        dt = datetime.fromisoformat(e[3])
                        msg += "📌 " + e[2] + " → " + dt.strftime("%H:%M") + "\n"

                    try:
                        await app.bot.send_message(chat_id=chat_id, text=msg)
                    except:
                        pass

        await asyncio.sleep(60)

# ================= MAIN ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # background task (NO job_queue, NO threads)
    asyncio.get_event_loop().create_task(morning_summary(app))

    print("Bot running with MORNING SUMMARY")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
