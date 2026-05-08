import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

# ---------------- CONFIG ---------------- #

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

    # time (HH:MM)
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

    # clean title
    title = text
    for w in words:
        if w in ["tomorrow", "today"] or ":" in w or "-" in w:
            title = title.replace(w, "")

    return title.strip(), dt


# ---------------- DB ---------------- #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()


def get_events(chat_id):
    cursor.execute(
        "SELECT title, event_time FROM events WHERE chat_id=? ORDER BY event_time",
        (chat_id,),
    )
    return cursor.fetchall()


# ---------------- HANDLERS ---------------- #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    title, dt = parse_natural(text)

    if not dt:
        await update.message.reply_text(
            "❌ Niet begrepen.\nVoorbeeld: dentist tomorrow 14:00"
        )
        return

    add_event(chat_id, title, dt)

    await update.message.reply_text(
        f"✅ Toegevoegd:\n{title}\n🕒 {dt.strftime('%d-%m %H:%M')}"
    )


async def show_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    events = get_events(chat_id)

    if not events:
        await update.message.reply_text("Geen afspraken gevonden.")
        return

    msg = "📅 Agenda:\n\n"
    for title, event_time in events:
        msg += f"- {title} → {event_time}\n"

    await update.message.reply_text(msg)


# ---------------- MAIN (STABLE) ---------------- #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    app.add_handler(
        MessageHandler(filters.Regex("^/agenda$"), show_agenda)
    )

    print("Bot is running...")

    # IMPORTANT: no asyncio, no loops, no post_init
    app.run_polling()


if __name__ == "__main__":
    main()
