import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ================= CONFIG ================= #

TOKEN = os.getenv("TOKEN")
NL_TZ = ZoneInfo("Europe/Amsterdam")
PORT = int(os.environ.get("PORT", 10000))
RENDER_URL = os.getenv("RENDER_URL")

# ================= DATABASE ================= #

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

# ================= SMART PARSER ================= #

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

    # ---------------- TIME ---------------- #

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

    # ---------------- DATE ---------------- #

    event_date = None

    for day, idx in weekdays.items():
        if day in text:
            days_ahead = idx - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            event_date = (now + timedelta(days=days_ahead)).date()
            break

    if event_date is None:
        if "tomorrow" in text or "morgen" in text:
            event_date = (now + timedelta(days=1)).date()
        elif "overmorgen" in text:
            event_date = (now + timedelta(days=2)).date()
        else:
            event_date = now.date()

    dt = datetime(
        year=event_date.year,
        month=event_date.month,
        day=event_date.day,
        hour=hour,
        minute=minute,
        tzinfo=NL_TZ
    )

    # ---------------- TITLE CLEANUP ---------------- #

    title = text

    for day in weekdays.keys():
        title = title.replace(day, "")

    title = re.sub(r"\d{1,2}:\d{2}", "", title)
    title = re.sub(r"\b\d{1,2}\b", "", title)

    for w in ["tomorrow", "morgen", "overmorgen"]:
        title = title.replace(w, "")

    title = title.strip()

    if title == "":
        title = "event"

    return title, dt

# ================= DB HELPERS ================= #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()

def get_events(chat_id):
    cursor.execute(
        "SELECT * FROM events WHERE chat_id=? ORDER BY event_time",
        (chat_id,),
    )
    return cursor.fetchall()

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Calendar bot is active")

async def day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = [
        e for e in get_events(chat_id)
        if datetime.fromisoformat(e[3]).date() == now.date()
    ]

    msg = "📅 Today:\n\n"
    for e in events:
        dt = datetime.fromisoformat(e[3])
        msg += f"📌 {e[2]} → {dt.strftime('%H:%M')}\n"

    await update.message.reply_text(msg if events else "No events today")

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = [
        e for e in get_events(chat_id)
        if now.date() <= datetime.fromisoformat(e[3]).date() <= now.date() + timedelta(days=7)
    ]

    msg = "📆 This week:\n\n"
    for e in events:
        dt = datetime.fromisoformat(e[3])
        msg += f"📌 {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"

    await update.message.reply_text(msg if events else "No events this week")

async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = [
        e for e in get_events(chat_id)
        if datetime.fromisoformat(e[3]).month == now.month
    ]

    msg = "🗓 This month:\n\n"
    for e in events:
        dt = datetime.fromisoformat(e[3])
        msg += f"📌 {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"

    await update.message.reply_text(msg if events else "No events this month")

# ================= MESSAGE HANDLER ================= #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    title, dt = parse(text)

    if not dt:
        await update.message.reply_text("❌ Could not understand. Example: Sunday 19:30 dinner")
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Event added\n"
        f"📌 {title}\n"
        f"🕒 {dt.strftime('%A %d %B %H:%M')}"
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
                        text=f"🔔 Reminder\n📌 {e[2]}\n🕒 {dt.strftime('%H:%M')}"
                    )

                    cursor.execute(
                        "UPDATE events SET reminded=1 WHERE id=?",
                        (e[0],),
                    )
                    conn.commit()

                except:
                    pass

        time.sleep(30)

# ================= MAIN ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    app.bot.delete_webhook(drop_pending_updates=True)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("month", month))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    threading.Thread(target=reminder_loop, args=(app,), daemon=True).start()

    print("Bot running (FINAL VERSION)")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
