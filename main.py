import os
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
NL_TZ = ZoneInfo("Europe/Amsterdam")

# Render provides PORT but we DON'T need webhook anymore in this stable version
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
    else:
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

    return text, datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        hour,
        minute,
        tzinfo=NL_TZ
    )

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

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Calendar bot is running")

async def agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Choose view:", reply_markup=agenda_menu())

# ================= CALLBACKS (FIXED CORE) ================= #

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat.id
    now = datetime.now(NL_TZ)

    data = q.data

    if data.startswith("del_"):
        delete_event(int(data.split("_")[1]))
        await q.message.reply_text("🗑 Deleted")
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
        await q.message.reply_text(title + "\n\nNo events")
        return

    msg = title + "\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        msg += "📌 " + e[2] + " → " + dt.strftime("%d-%m %H:%M") + "\n"
        msg += "🆔 /delete " + str(e[0]) + "\n\n"

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
        "✅ Added\n📌 " + title + "\n🕒 " + dt.strftime("%A %d %B %H:%M")
    )

# ================= SCHEDULER (NO THREADS) ================= #

async def reminder_job(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(NL_TZ)

    cursor.execute("SELECT * FROM events")
    events = cursor.fetchall()

    for e in events:
        if e[4]:
            continue

        dt = datetime.fromisoformat(e[3])

        if now >= dt - timedelta(minutes=15):
            try:
                await context.bot.send_message(
                    chat_id=e[1],
                    text="🔔 Reminder\n📌 " + e[2] + "\n🕒 " + dt.strftime("%H:%M")
                )

                cursor.execute("UPDATE events SET reminded=1 WHERE id=?", (e[0],))
                conn.commit()
            except:
                pass

# ================= MAIN ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("agenda", agenda))

    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # stable job queue instead of threads
    app.job_queue.run_repeating(reminder_job, interval=30, first=10)

    print("Bot running V5 stable")

    app.run_polling()

if __name__ == "__main__":
    main()
