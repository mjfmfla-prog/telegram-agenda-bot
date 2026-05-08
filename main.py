import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

# ---------------- CONFIG ---------------- #

TOKEN = os.getenv("TOKEN")
NL_TZ = ZoneInfo("Europe/Amsterdam")

# ---------------- KEEP ALIVE (Render/Replit) ---------------- #

PORT = int(os.environ.get("PORT", 10000))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot alive")

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

# ---------------- AI-LIKE PARSER ---------------- #

def parse_natural(text: str):
    text = text.lower()

    now = datetime.now(NL_TZ)

    # time detect
    time_match = re.search(r"(\d{1,2}:\d{2})", text)
    if not time_match:
        return None, None

    time = datetime.strptime(time_match.group(1), "%H:%M").time()

    # date detect
    if "tomorrow" in text:
        date = now + timedelta(days=1)
    elif "today" in text:
        date = now
    else:
        date = now

    dt = datetime.combine(date.date(), time).replace(tzinfo=NL_TZ)

    # title cleanup
    title = text
    title = title.replace("tomorrow", "").replace("today", "")
    title = re.sub(r"\d{1,2}:\d{2}", "", title)

    return title.strip(), dt

# ---------------- DB FUNCTIONS ---------------- #

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

def edit_event(event_id, new_title):
    cursor.execute("UPDATE events SET title=? WHERE id=?", (new_title, event_id))
    conn.commit()

# ---------------- REMINDER LOOP ---------------- #

def reminder_loop(app):
    while True:
        now = datetime.now(NL_TZ)

        cursor.execute("SELECT id, chat_id, title, event_time, reminded FROM events")
        events = cursor.fetchall()

        for event in events:
            event_id, chat_id, title, event_time, reminded = event

            if reminded:
                continue

            dt = datetime.fromisoformat(event_time)
            reminder_time = dt - timedelta(minutes=15)

            if now >= reminder_time:
                try:
                    app.bot.send_message(
                        chat_id=chat_id,
                        text=f"🔔 Reminder: {title}\n🕒 {dt.strftime('%d-%m %H:%M')}"
                    )

                    cursor.execute(
                        "UPDATE events SET reminded=1 WHERE id=?",
                        (event_id,),
                    )
                    conn.commit()

                except Exception as e:
                    print("Reminder error:", e)

        time.sleep(30)

# ---------------- MORNING SUMMARY ---------------- #

def morning_loop(app):
    sent_today = set()

    while True:
        now = datetime.now(NL_TZ)

        if now.hour == 8 and now.date() not in sent_today:
            cursor.execute("SELECT DISTINCT chat_id FROM events")
            chats = cursor.fetchall()

            for (chat_id,) in chats:
                cursor.execute(
                    "SELECT title, event_time FROM events WHERE chat_id=?",
                    (chat_id,),
                )
                events = cursor.fetchall()

                today_events = [
                    e for e in events
                    if datetime.fromisoformat(e[1]).date() == now.date()
                ]

                if today_events:
                    msg = "🌅 Vandaag je agenda:\n\n"
                    for title, t in today_events:
                        dt = datetime.fromisoformat(t)
                        msg += f"- {title} → {dt.strftime('%H:%M')}\n"

                    try:
                        app.bot.send_message(chat_id=chat_id, text=msg)
                    except:
                        pass

            sent_today.add(now.date())

        time.sleep(60)

# ---------------- COMMANDS ---------------- #

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

async def agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    events = get_events(chat_id)

    if not events:
        await update.message.reply_text("Geen events.")
        return

    msg = "📅 Agenda:\n\n"
    for e in events:
        dt = datetime.fromisoformat(e[2])
        msg += f"{e[0]} - {e[1]} → {dt.strftime('%d-%m %H:%M')}\n"

    await update.message.reply_text(msg)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        event_id = int(context.args[0])
        delete_event(event_id)
        await update.message.reply_text("🗑 Event verwijderd")
    except:
        await update.message.reply_text("Gebruik: /delete ID")

async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        event_id = int(context.args[0])
        new_title = " ".join(context.args[1:])
        edit_event(event_id, new_title)
        await update.message.reply_text("✏ Event aangepast")
    except:
        await update.message.reply_text("Gebruik: /edit ID nieuwe titel")

# ---------------- MAIN ---------------- #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("agenda", agenda))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("edit", edit_cmd))

    threading.Thread(target=reminder_loop, args=(app,), daemon=True).start()
    threading.Thread(target=morning_loop, args=(app,), daemon=True).start()

    print("Bot running...")

    app.run_polling()

if __name__ == "__main__":
    main()
