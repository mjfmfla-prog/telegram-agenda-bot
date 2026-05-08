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
TZ = ZoneInfo("Europe/Amsterdam")

# ================= DB ================= #

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

# ================= DATE PARSER (FIXED) ================= #

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

    # 1. TIME
    match = re.search(r"(\d{1,2}):(\d{2})", raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
    else:
        return None, None

    # 2. DATE (FIXED PRIORITY SYSTEM)

    event_date = None

    # A) FULL DATE like "20 may", "20 mei"
    date_match = re.search(r"(\d{1,2})\s+([a-z]+)", raw)
    if date_match:
        day = int(date_match.group(1))
        month_text = date_match.group(2)

        month = MONTHS.get(month_text)

        if month:
            year = now.year
            event_date = datetime(year, month, day).date()

    # B) WEEKDAY fallback ONLY if no real date
    if event_date is None:
        for d, idx in WEEKDAYS.items():
            if d in raw:
                diff = idx - now.weekday()
                if diff <= 0:
                    diff += 7
                event_date = (now + timedelta(days=diff)).date()
                break

    # C) default today
    if event_date is None:
        event_date = now.date()

    dt = datetime(event_date.year, event_date.month, event_date.day, hour, minute, tzinfo=TZ)

    # 3. CLEAN TITLE
    title = raw

    # remove date parts
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

def edit_event(event_id, new_title, new_dt):
    cursor.execute(
        "UPDATE events SET title=?, event_time=? WHERE id=?",
        (new_title, new_dt.isoformat(), event_id),
    )
    conn.commit()

def get_events(chat_id):
    cursor.execute("SELECT * FROM events WHERE chat_id=? ORDER BY event_time", (chat_id,))
    return cursor.fetchall()

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Calendar bot running")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    title, dt = parse(text)

    if not dt:
        await update.message.reply_text("❌ Example: Wednesday 20 May 14:30 haircut Michelle")
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        "✅ Added\n📌 " + title +
        "\n🕒 " + dt.strftime("%A %d %B %H:%M")
    )

# ================= DELETE ================= #

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ /delete <id>")
        return

    try:
        event_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid ID")
        return

    delete_event(event_id)

    await update.message.reply_text("🗑 Deleted event")

# ================= EDIT ================= #

async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ /edit <id> new text")
        return

    try:
        event_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Invalid ID")
        return

    new_text = " ".join(context.args[1:])

    title, dt = parse(new_text)

    if not dt:
        await update.message.reply_text("❌ Could not parse new event")
        return

    edit_event(event_id, title, dt)

    await update.message.reply_text(
        "✏️ Updated\n📌 " + title +
        "\n🕒 " + dt.strftime("%A %d %B %H:%M")
    )

# ================= VIEW ================= #

async def day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    events = get_events(update.effective_chat.id)

    filtered = [e for e in events if datetime.fromisoformat(e[3]).date() == now.date()]

    msg = "📅 Today\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        msg += f"🆔 {e[0]} | 🕒 {dt.strftime('%H:%M')} {e[2]}\n"

    await update.message.reply_text(msg if filtered else "No events")

# ================= MAIN ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("edit", edit))
    app.add_handler(CommandHandler("day", day))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add))

    print("Calendar bot running FIXED VERSION")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}",
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
