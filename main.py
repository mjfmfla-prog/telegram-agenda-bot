import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ================= CONFIG ================= #

TOKEN = os.getenv("TOKEN")
NL_TZ = ZoneInfo("Europe/Amsterdam")
PORT = int(os.environ.get("PORT", 10000))

# ================= WEB SERVER (RENDER) ================= #

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_server():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=run_server, daemon=True).start()

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

    # ---------------- TIME ---------------- #

    hour = None
    minute = 0

    # 13:00
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))

    # "13" → 13:00 (BELANGRIJK)
    if hour is None:
        match = re.search(r"\b(\d{1,2})\b", text)
        if match:
            hour = int(match.group(1))
            minute = 0

    if hour is None:
        return None, None

    # ---------------- DATE ---------------- #

    event_date = now.date()

    if "tomorrow" in text or "morgen" in text:
        event_date = (now + timedelta(days=1)).date()

    elif "overmorgen" in text:
        event_date = (now + timedelta(days=2)).date()

    # ---------------- DATETIME ---------------- #

    dt = datetime(
        year=event_date.year,
        month=event_date.month,
        day=event_date.day,
        hour=hour,
        minute=minute,
        tzinfo=NL_TZ
    )

    # ---------------- TITLE ---------------- #

    title = text
    title = re.sub(r"\d{1,2}:\d{2}", "", title)
    title = re.sub(r"\b\d{1,2}\b", "", title)

    remove_words = ["tomorrow", "morgen", "overmorgen", "meeting"]

    for w in remove_words:
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

# ================= FORMATTING ================= #

def format_events(events, title):
    if not events:
        return f"❌ Geen events voor {title}"

    msg = f"{title}:\n\n"

    for e in events:
        dt = datetime.fromisoformat(e[3])

        msg += (
            f"📌 {e[2]}\n"
            f"🕒 {dt.strftime('%d-%m %H:%M')}\n"
            f"🆔 ID: {e[0]}\n\n"
        )

    return msg

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Agenda bot actief")

async def day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = [
        e for e in get_events(chat_id)
        if datetime.fromisoformat(e[3]).date() == now.date()
    ]

    await update.message.reply_text(format_events(events, "📅 Vandaag"))

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = [
        e for e in get_events(chat_id)
        if now.date() <= datetime.fromisoformat(e[3]).date() <= now.date() + timedelta(days=7)
    ]

    await update.message.reply_text(format_events(events, "📆 Deze week"))

async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = [
        e for e in get_events(chat_id)
        if datetime.fromisoformat(e[3]).month == now.month
    ]

    await update.message.reply_text(format_events(events, "🗓 Deze maand"))

# ================= MESSAGE HANDLER ================= #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    title, dt = parse(text)

    if not dt:
        await update.message.reply_text("❌ Ongeldig. Voorbeeld: tomorrow 13 meeting")
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Toegevoegd\n📌 {title}\n🕒 {dt.strftime('%d-%m %H:%M')}"
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

# ================= MAIN (WEBHOOK) ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    # webhook fix (BELANGRIJK)
    app.bot.delete_webhook(drop_pending_updates=True)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("month", month))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    threading.Thread(target=reminder_loop, args=(app,), daemon=True).start()

    print("Bot running (WEBHOOK MODE)")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://YOUR-RENDER-URL.onrender.com/{TOKEN}"
    )

if __name__ == "__main__":
    main()
