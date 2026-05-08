import os
import re
import sqlite3
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
TZ = ZoneInfo("Europe/Amsterdam")

# ================= DATABASE ================= #

conn = sqlite3.connect("agenda.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    title TEXT,
    event_time TEXT
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

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "mei": 5, "juni": 6, "juli": 7, "oktober": 10, "december": 12
}

def parse(text: str):
    raw = text.lower().strip()
    now = datetime.now(TZ)

    # ================= TIME ================= #
    match = re.search(r"(\d{1,2}):(\d{2})", raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
    else:
        hour = 9
        minute = 0

    # ================= DATE ================= #
    event_date = None

    # 1. full date (20 may)
    date_match = re.search(r"(\d{1,2})\s+([a-z]+)", raw)
    if date_match:
        day = int(date_match.group(1))
        month = MONTHS.get(date_match.group(2))
        if month:
            event_date = datetime(now.year, month, day).date()

    # 2. weekday fallback
    if event_date is None:
        for d, idx in WEEKDAYS.items():
            if d in raw:
                diff = idx - now.weekday()
                if diff <= 0:
                    diff += 7
                event_date = (now + timedelta(days=diff)).date()
                break

    # 3. default today
    if event_date is None:
        event_date = now.date()

    dt = datetime(event_date.year, event_date.month, event_date.day, hour, minute, tzinfo=TZ)

    # ================= CLEAN TITLE ================= #
    title = raw

    title = re.sub(r"\d{1,2}:\d{2}", "", title)
    title = re.sub(r"\d{1,2}\s+[a-z]+", "", title)

    for d in WEEKDAYS.keys():
        title = title.replace(d, "")

    title = re.sub(r"\s+", " ", title).strip()

    if not title:
        title = "event"

    return title, dt

# ================= DB ================= #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()

def delete_event(event_id):
    cursor.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()

def edit_event(event_id, title, dt):
    cursor.execute(
        "UPDATE events SET title=?, event_time=? WHERE id=?",
        (title, dt.isoformat(), event_id),
    )
    conn.commit()

def get_events(chat_id):
    cursor.execute("SELECT * FROM events WHERE chat_id=? ORDER BY event_time", (chat_id,))
    return cursor.fetchall()

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Calendar bot running")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title, dt = parse(update.message.text)

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        "✅ Added\n📌 " + title +
        "\n🕒 " + dt.strftime("%A %d %B %H:%M")
    )

# ================= WEEK (GOOGLE STYLE) ================= #

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)

    start = now.date() - timedelta(days=now.weekday())
    end = start + timedelta(days=6)

    events = get_events(update.effective_chat.id)

    filtered = [
        e for e in events
        if start <= datetime.fromisoformat(e[3]).date() <= end
    ]

    grouped = {}

    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        grouped.setdefault(dt.date(), []).append((e, dt))

    grouped = dict(sorted(grouped.items()))

    msg = f"📆 Week ({start.strftime('%d %B')} - {end.strftime('%d %B')})\n\n"

    if not grouped:
        await update.message.reply_text(msg + "No events")
        return

    for day, items in grouped.items():
        msg += "━━━━━━━━━━━━━━\n"
        msg += f"📅 {day.strftime('%A %d %B')}\n"
        msg += "━━━━━━━━━━━━━━\n"

        for e, dt in items:
            msg += f"🕒 {dt.strftime('%H:%M')} {e[2]}\n"

        msg += "\n"

    await update.message.reply_text(msg)

# ================= DAY ================= #

async def day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    events = get_events(update.effective_chat.id)

    filtered = [e for e in events if datetime.fromisoformat(e[3]).date() == now.date()]

    msg = "📅 Today\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        msg += f"🕒 {dt.strftime('%H:%M')} {e[2]}\n"

    await update.message.reply_text(msg if filtered else "No events")

# ================= MONTH ================= #

async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    events = get_events(update.effective_chat.id)

    filtered = [
        e for e in events
        if datetime.fromisoformat(e[3]).month == now.month
    ]

    msg = "🗓 This Month\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        msg += f"📅 {dt.strftime('%d %B')} 🕒 {dt.strftime('%H:%M')} {e[2]}\n"

    await update.message.reply_text(msg if filtered else "No events")

# ================= DELETE ================= #

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ /delete <id>")
        return

    delete_event(int(context.args[0]))
    await update.message.reply_text("🗑 Deleted")

# ================= EDIT ================= #

async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ /edit <id> new event")
        return

    event_id = int(context.args[0])
    text = " ".join(context.args[1:])

    title, dt = parse(text)
    edit_event(event_id, title, dt)

    await update.message.reply_text("✏️ Updated")

# ================= MAIN ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("edit", edit))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Calendar bot running FINAL VERSION")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
