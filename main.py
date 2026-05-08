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

# ================= CONFIG ================= #

TOKEN = os.getenv("TOKEN")

NL_TZ = ZoneInfo("Europe/Amsterdam")

PORT = int(os.environ.get("PORT", 10000))

# ================= KEEP ALIVE ================= #

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

# ================= PARSER ================= #

def parse(text: str):
    text = text.lower().strip()

    now = datetime.now(NL_TZ)

    # Zoek tijd
    match = re.search(r"(\d{1,2}):(\d{2})", text)

    if not match:
        return None, None

    try:
        hour = int(match.group(1))
        minute = int(match.group(2))
    except:
        return None, None

    # Datum bepalen
    event_date = now.date()

    if "tomorrow" in text:
        event_date = (now + timedelta(days=1)).date()

    # Datetime maken
    dt = datetime(
        year=event_date.year,
        month=event_date.month,
        day=event_date.day,
        hour=hour,
        minute=minute,
        tzinfo=NL_TZ
    )

    # Titel schoonmaken
    title = re.sub(r"\d{1,2}:\d{2}", "", text)
    title = title.replace("tomorrow", "")
    title = title.replace("today", "")
    title = title.strip()

    if title == "":
        title = "event"

    return title, dt

# ================= DATABASE HELPERS ================= #

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
    cursor.execute(
        "DELETE FROM events WHERE id=?",
        (event_id,),
    )
    conn.commit()

# ================= MENU ================= #

def menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Vandaag", callback_data="today")],
        [InlineKeyboardButton("📆 Deze week", callback_data="week")]
    ])

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📅 Agenda bot actief",
        reply_markup=menu_keyboard()
    )

async def agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    now = datetime.now(NL_TZ)

    events = get_events(chat_id)

    today_events = [
        e for e in events
        if datetime.fromisoformat(e[3]).date() == now.date()
    ]

    if not today_events:
        await update.message.reply_text("Geen events vandaag")
        return

    msg = "📅 Vandaag:\n\n"

    for e in today_events:
        dt = datetime.fromisoformat(e[3])

        msg += f"{e[0]} - {e[2]} → {dt.strftime('%H:%M')}\n"

    await update.message.reply_text(msg)

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    now = datetime.now(NL_TZ)

    events = get_events(chat_id)

    week_events = [
        e for e in events
        if now.date()
        <= datetime.fromisoformat(e[3]).date()
        <= now.date() + timedelta(days=7)
    ]

    if not week_events:
        await update.message.reply_text("Geen events deze week")
        return

    msg = "📆 Deze week:\n\n"

    for e in week_events:
        dt = datetime.fromisoformat(e[3])

        msg += f"{e[0]} - {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"

    await update.message.reply_text(msg)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        event_id = int(context.args[0])

        delete_event(event_id)

        await update.message.reply_text("🗑 Event verwijderd")

    except:
        await update.message.reply_text("Gebruik: /delete ID")

# ================= BUTTONS ================= #

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    chat_id = query.message.chat.id

    now = datetime.now(NL_TZ)

    events = get_events(chat_id)

    if query.data == "today":

        filtered = [
            e for e in events
            if datetime.fromisoformat(e[3]).date() == now.date()
        ]

        title = "📅 Vandaag"

    else:

        filtered = [
            e for e in events
            if now.date()
            <= datetime.fromisoformat(e[3]).date()
            <= now.date() + timedelta(days=7)
        ]

        title = "📆 Deze week"

    if not filtered:
        await query.message.reply_text("Geen events")
        return

    msg = f"{title}:\n\n"

    for e in filtered:
        dt = datetime.fromisoformat(e[3])

        msg += f"{e[0]} - {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"

    await query.message.reply_text(msg)

# ================= MESSAGE HANDLER ================= #

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    print("TEXT:", text)

    title, dt = parse(text)

    print("PARSED:", title, dt)

    if not dt:
        await update.message.reply_text(
            "❌ Gebruik bijvoorbeeld:\n"
            "meeting 14:00\n"
            "dentist tomorrow 09:30"
        )
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Toegevoegd:\n"
        f"{title}\n"
        f"🕒 {dt.strftime('%d-%m %H:%M')}"
    )

# ================= REMINDERS ================= #

def reminder_loop(app):
    while True:

        now = datetime.now(NL_TZ)

        cursor.execute("SELECT * FROM events")

        events = cursor.fetchall()

        for e in events:

            reminded = e[4]

            if reminded:
                continue

            dt = datetime.fromisoformat(e[3])

            reminder_time = dt - timedelta(minutes=15)

            if now >= reminder_time:

                try:
                    app.bot.send_message(
                        chat_id=e[1],
                        text=(
                            f"🔔 Reminder\n\n"
                            f"{e[2]}\n"
                            f"🕒 {dt.strftime('%H:%M')}"
                        )
                    )

                    cursor.execute(
                        "UPDATE events SET reminded=1 WHERE id=?",
                        (e[0],),
                    )

                    conn.commit()

                except Exception as ex:
                    print("Reminder error:", ex)

        time.sleep(30)

# ================= MORNING SUMMARY ================= #

def morning_loop(app):

    sent_days = set()

    while True:

        now = datetime.now(NL_TZ)

        if now.hour == 8 and now.date() not in sent_days:

            cursor.execute("SELECT DISTINCT chat_id FROM events")

            chats = cursor.fetchall()

            for c in chats:

                chat_id = c[0]

                events = get_events(chat_id)

                today_events = [
                    e for e in events
                    if datetime.fromisoformat(e[3]).date() == now.date()
                ]

                if today_events:

                    msg = "🌅 Vandaag:\n\n"

                    for e in today_events:

                        dt = datetime.fromisoformat(e[3])

                        msg += f"- {e[2]} → {dt.strftime('%H:%M')}\n"

                    try:
                        app.bot.send_message(chat_id=chat_id, text=msg)

                    except Exception as ex:
                        print("Morning summary error:", ex)

            sent_days.add(now.date())

        time.sleep(60)

# ================= MAIN ================= #

def main():

    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("agenda", agenda))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("delete", delete_cmd))

    # Buttons
    app.add_handler(CallbackQueryHandler(button_handler))

    # Messages
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    # Background loops
    threading.Thread(
        target=reminder_loop,
        args=(app,),
        daemon=True
    ).start()

    threading.Thread(
        target=morning_loop,
        args=(app,),
        daemon=True
    ).start()

    print("Bot running...")

    app.run_polling(drop_pending_updates=True)

# ================= START ================= #

if __name__ == "__main__":
    main()
