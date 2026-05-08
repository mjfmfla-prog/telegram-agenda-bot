import os
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, ContextTypes, filters

# ---------------- CONFIG ---------------- #

TOKEN = os.getenv("TOKEN")
NL_TZ = ZoneInfo("Europe/Amsterdam")

PORT = int(os.environ.get("PORT", 10000))

# ---------------- KEEP ALIVE ---------------- #

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_server():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

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

def parse(text: str):
    text = text.lower()
    now = datetime.now(NL_TZ)

    if ":" not in text:
        return None, None

    try:
        time_part = [w for w in text.split() if ":" in w][0]
        time = datetime.strptime(time_part, "%H:%M").time()
    except:
        return None, None

    date = now

    if "tomorrow" in text:
        date = now + timedelta(days=1)

    dt = datetime.combine(date.date(), time).replace(tzinfo=NL_TZ)

    title = text.replace("tomorrow", "").replace(time_part, "").strip()

    return title, dt

# ---------------- DB ---------------- #

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

def delete_event(event_id):
    cursor.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()

# ---------------- MENU ---------------- #

def menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Agenda", callback_data="agenda")],
        [InlineKeyboardButton("ℹ Help", callback_data="help")]
    ])

# ---------------- HANDLERS ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Agenda bot actief", reply_markup=menu_keyboard())

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Menu:", reply_markup=menu_keyboard())

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id

    if query.data == "agenda":
        events = get_events(chat_id)

        if not events:
            await query.message.reply_text("Geen events")
            return

        msg = "📅 Agenda:\n\n"
        for e in events:
            dt = datetime.fromisoformat(e[2])
            msg += f"{e[0]} - {e[1]} → {dt.strftime('%d-%m %H:%M')}\n"

        await query.message.reply_text(msg)

    elif query.data == "help":
        await query.message.reply_text(
            "Stuur:\n"
            "meeting 14:00\n"
            "tomorrow dentist 09:00"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    title, dt = parse(text)

    if not dt:
        await update.message.reply_text("❌ Gebruik: meeting 14:00")
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Toegevoegd:\n{title}\n🕒 {dt.strftime('%d-%m %H:%M')}"
    )

# ---------------- MAIN ---------------- #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot running...")

    app.run_polling()

if __name__ == "__main__":
    main()
