import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ---------------- #

TOKEN = os.getenv("TOKEN")
NL_TZ = ZoneInfo("Europe/Amsterdam")
PORT = int(os.environ.get("PORT", 10000))

# ---------------- KEEP ALIVE (Render fix) ---------------- #

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

# ---------------- FIXED ROBUST PARSER ---------------- #

def parse(text: str):
    text = text.lower()
    now = datetime.now(NL_TZ)

    # TIME detection (robust)
    time_match = re.search(r"\b(\d{1,2}:\d{2})\b", text)
    if not time_match:
        return None, None

    try:
        hour, minute = map(int, time_match.group(1).split(":"))
        t = datetime(now.year, now.month, now.day, hour, minute).time()
    except:
        return None, None

    # DATE handling
    date = now

    if "tomorrow" in text:
        date = now + timedelta(days=1)
    elif "today" in text:
        date = now

    dt = datetime.combine(date.date(), t).replace(tzinfo=NL_TZ)

    # TITLE cleanup
    title = text
    title = title.replace("tomorrow", "")
    title = title.replace("today", "")
    title = re.sub(r"\b\d{1,2}:\d{2}\b", "", title)
    title = title.strip()

    if not title:
        title = "event"

    return title, dt

# ---------------- DB HELPERS ---------------- #

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

def delete_event(event_id):
    cursor.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()

# ---------------- MENU ---------------- #

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Vandaag", callback_data="today")],
        [InlineKeyboardButton("📆 Week", callback_data="week")]
    ])

# ---------------- HANDLERS ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Agenda bot actief", reply_markup=menu())

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat.id
    now = datetime.now(NL_TZ)

    events = get_events(chat_id)

    if q.data == "today":
        filtered = [
            e for e in events
            if datetime.fromisoformat(e[3]).date() == now.date()
        ]
        title = "📅 Vandaag"

    else:
        filtered = [
            e for e in events
            if now.date() <= datetime.fromisoformat(e[3]).date() <= now.date() + timedelta(days=7)
        ]
        title = "📆 Week"

    msg = f"{title}:\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        msg += f"{e[0]} - {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"

    await q.message.reply_text(msg or "Geen events")

# ---------------- MESSAGE INPUT ---------------- #

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

# ---------------- COMMANDS ---------------- #

async def agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = get_events(chat_id)

    today = [
        e for e in events
        if datetime.fromisoformat(e[3]).date() == now.date()
    ]

    msg = "📅 Vandaag:\n\n"

    for e in today:
        dt = datetime.fromisoformat(e[3])
        msg += f"{e[0]} - {e[2]} → {dt.strftime('%H:%M')}\n"

    await update.message.reply_text(msg or "Geen events vandaag")

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now(NL_TZ)

    events = get_events(chat_id)

    week_events = [
        e for e in events
        if now.date() <= datetime.fromisoformat(e[3]).date() <= now.date() + timedelta(days=7)
    ]

    msg = "📆 Week:\n\n"

    for e in week_events:
        dt = datetime.fromisoformat(e[3])
        msg += f"{e[0]} - {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"

    await update.message.reply_text(msg or "Geen events deze week")

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        delete_event(int(context.args[0]))
        await update.message.reply_text("🗑 verwijderd")
    except:
        await update.message.reply_text("Gebruik: /delete ID")

# ---------------- REMINDERS ---------------- #

def reminder_loop(app):
    while True:
        now = datetime.now(NL_TZ)

        cursor.execute("SELECT * FROM events")
        events = cursor.fetchall()

        for e in events:
            if e[4]:
                continue

            dt = datetime.fromisoformat(e[3])
            reminder_time = dt - timedelta(minutes=15)

            if now >= reminder_time:
                try:
                    app.bot.send_message(
                        chat_id=e[1],
                        text=f"🔔 Reminder:\n{e[2]}\n🕒 {dt.strftime('%H:%M')}"
                    )

                    cursor.execute("UPDATE events SET reminded=1 WHERE id=?", (e[0],))
                    conn.commit()

                except:
                    pass

        time.sleep(30)

# ---------------- MORNING SUMMARY ---------------- #

def morning_loop(app):
    sent = set()

    while True:
        now = datetime.now(NL_TZ)

        if now.hour == 8 and now.date() not in sent:

            cursor.execute("SELECT DISTINCT chat_id FROM events")
            chats = cursor.fetchall()

            for c in chats:

                events = get_events(c[0])

                today = [
                    e for e in events
                    if datetime.fromisoformat(e[3]).date() == now.date()
                ]

                if today:
                    msg = "🌅 Vandaag:\n\n"

                    for e in today:
                        dt = datetime.fromisoformat(e[3])
                        msg += f"- {e[2]} → {dt.strftime('%H:%M')}\n"

                    app.bot.send_message(c[0], msg)

            sent.add(now.date())

        time.sleep(60)

# ---------------- MAIN ---------------- #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("agenda", agenda))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    threading.Thread(target=reminder_loop, args=(app,), daemon=True).start()
    threading.Thread(target=morning_loop, args=(app,), daemon=True).start()

    print("Bot running...")

    app.run_polling()

if __name__ == "__main__":
    main()
