import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ---------------- CONFIG ---------------- #

TOKEN = os.getenv("TOKEN")

# ---------------- KEEP ALIVE SERVER ---------------- #

PORT = int(os.environ.get("PORT", 10000))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"BOT RUNNING")

def run_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ---------------- DATABASE (NIET BELANGRIJK HIER) ---------------- #

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

# ---------------- 🔥 DEBUG HANDLER (BELANGRIJK) ---------------- #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("HANDLER TRIGGERED")  # zichtbaar in logs

    await update.message.reply_text(
        "🟢 NIEUWE CODE DRAAIT CORRECT\n"
        f"Je stuurde: {update.message.text}"
    )

# ---------------- MAIN ---------------- #

def main():
    print("STARTING BOT...")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    app.run_polling()

if __name__ == "__main__":
    main()
