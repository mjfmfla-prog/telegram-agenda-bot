import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

TOKEN = os.getenv("TOKEN")
NL_TZ = ZoneInfo("Europe/Amsterdam")

# ---------------- DATABASE ---------------- #

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


# ---------------- PARSER ---------------- #

def parse_natural(text: str):
    text = text.lower()
    now = datetime.now(NL_TZ)

    date = None
    time = None

    words = text.split()

    # time HH:MM
    for w in words:
        if ":" in w:
            try:
                time = datetime.strptime(w, "%H:%M").time()
            except:
                pass

    # date keywords
    if "tomorrow" in text:
        date = now + timedelta(days=1)
    elif "today" in text:
        date = now
    else:
        for w in words:
            if "-" in w and len(w) == 5:
                try:
                    date = datetime.strptime(w + f"-{now.year}", "%d-%m-%Y")
                except:
                    pass

    if not date or not time:
        return None, None

    dt = datetime.combine(date.date(), time).replace(tzinfo=NL_TZ)

    title = text
    for w in words:
        if w in ["tomorrow", "today"] or ":" in w or "-" in w:
            title = title.replace(w, "")

    return title.strip(), dt


# ---------------- DATABASE FUNCTIONS ---------------- #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()


def get_pending_events():
    cursor.execute(
        "SELECT id, chat_id, title, event_time FROM events WHERE reminded = 0"
    )
    return cursor.fetchall()


def mark_reminded(event_id):
    cursor.execute(
        "UPDATE events SET reminded = 1 WHERE id = ?",
        (event_id,),
    )
    conn.commit()


# ---------------- MESSAGE HANDLER ---------------- #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    title, dt = parse_natural(text)

    if not dt:
        await update.message.reply_text("❌ Try: 'dentist tomorrow 14:00'")
        return

    add_event(chat_id, title, dt)

    await update.message.reply_text(
        f"✅ Added:\n{title}\n🕒 {dt.strftime('%d-%m %H:%M')}"
    )


# ---------------- REMINDER LOOP ---------------- #

async def reminder_loop(app):
    while True:
        now = datetime.now(NL_TZ)
        events = get_pending_events()

        for event in events:
            event_id, chat_id, title, event_time = event
            event_dt = datetime.fromisoformat(event_time)

            reminder_time = event_dt - timedelta(minutes=15)

            if now >= reminder_time:
                try:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=f"🔔 Reminder: {title}\n🕒 {event_dt.strftime('%d-%m %H:%M')}"
                    )
                    mark_reminded(event_id)
                except Exception as e:
                    print("Error sending reminder:", e)

        await asyncio.sleep(30)


# ---------------- MAIN (FIXED FOR RENDER) ---------------- #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("Bot is running...")

    # background task SAFE way (NO asyncio.run, NO loop errors)
    async def start_tasks():
        asyncio.create_task(reminder_loop(app))

    app.post_init = start_tasks

    app.run_polling()


if __name__ == "__main__":
    main()
