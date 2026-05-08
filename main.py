import os
import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

TOKEN = os.getenv("TOKEN")

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


# ---------------- SIMPLE NATURAL PARSER ---------------- #

def parse_natural(text: str):
    text = text.lower()
    now = datetime.now()

    date = None
    time = None

    words = text.split()

    # find time (HH:MM)
    for w in words:
        if ":" in w:
            try:
                time = datetime.strptime(w, "%H:%M").time()
            except:
                pass

    # detect date keywords
    if "tomorrow" in text:
        date = now + timedelta(days=1)
    elif "today" in text:
        date = now
    else:
        # detect dd-mm format
        for w in words:
            if "-" in w and len(w) == 5:
                try:
                    date = datetime.strptime(w + f"-{now.year}", "%d-%m-%Y")
                except:
                    pass

    if not date or not time:
        return None, None

    dt = datetime.combine(date.date(), time)

    # remove time/date words to get title
    title = text
    for w in words:
        if w in ["tomorrow", "today"] or ":" in w or "-" in w:
            title = title.replace(w, "")

    title = title.strip()

    return title, dt


# ---------------- DATABASE ---------------- #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()


def get_events(chat_id):
    cursor.execute(
        "SELECT id, title, event_time FROM events WHERE chat_id=? ORDER BY event_time",
        (chat_id,),
    )
    return cursor.fetchall()


# ---------------- MESSAGE HANDLER ---------------- #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    title, dt = parse_natural(text)

    if not dt:
        await update.message.reply_text(
            "❌ I didn't understand.\n"
            "Try: 'dentist tomorrow 14:00' or 'meeting Friday 10:00'"
        )
        return

    add_event(chat_id, title, dt)

    await update.message.reply_text(
        f"✅ Added:\n{title}\n🕒 {dt.strftime('%d-%m %H:%M')}"
    )


# ---------------- VIEW AGENDA (OPTIONAL COMMAND) ---------------- #

async def show_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    events = get_events(chat_id)

    if not events:
        await update.message.reply_text("No events found.")
        return

    msg = "📅 Agenda:\n\n"
    for e in events:
        msg += f"- {e[1]} → {e[2]}\n"

    await update.message.reply_text(msg)


# ---------------- MAIN ---------------- #

def main():
    app = Application.builder().token(TOKEN).build()

    # any normal message = event
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # optional: still allow /agenda
    app.add_handler(MessageHandler(filters.Regex("^/agenda$"), show_agenda))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
