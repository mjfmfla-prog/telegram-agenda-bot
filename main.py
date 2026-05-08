import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ---------------- CONFIG ---------------- #

TOKEN = os.getenv("TOKEN")
NL_TZ = ZoneInfo("Europe/Amsterdam")

# ---------------- KEEP ALIVE SERVER (RENDER FIX) ---------------- #

PORT = int(os.environ.get("PORT", 10000))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")

def run_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

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

    for w in words:
        if ":" in w:
            try:
                time = datetime.strptime(w, "%H:%M").time()
            except:
                pass

    if "tomorrow" in text:
        date = now + timedelta(days=1)
    elif "today" in text:
        date = now

    if not date or not time:
        return None, None

    dt = datetime.combine(date.date(), time).replace(tzinfo=NL_TZ)

    title = text
    for w in words:
        if w in ["tomorrow", "today"] or ":" in w:
            title = title.replace(w, "")

    return title.strip(), dt

# ---------------- DB ---------------- #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()

# ---------------- HANDLER ---------------- #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    title, dt = parse_natural(text)

    if not dt:
        await update.message.reply_text("❌ Gebruik: dentist tomorrow 14:00")
        return

    add_event(chat_id, title, dt)

    await update.message.reply_text(
        f"✅ Toegevoegd:\n{title}\n🕒 {dt.strftime('%d-%m %H:%M')}"
    )

# ---------------- MAIN ---------------- #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    print("Bot running on Render...")

    app.run_polling()

if __name__ == "__main__":
    main()
