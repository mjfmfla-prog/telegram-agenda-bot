import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ================= CONFIG ================= #

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
NL_TZ = ZoneInfo("Europe/Amsterdam")
PORT = int(os.environ.get("PORT", 10000))

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

    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5,
        "sunday": 6,
        "maandag": 0, "dinsdag": 1, "woensdag": 2,
        "donderdag": 3, "vrijdag": 4, "zaterdag": 5,
        "zondag": 6,
    }

    hour = None
    minute = 0

    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))

    if hour is None:
        match = re.search(r"\b(\d{1,2})\b", text)
        if match:
            hour = int(match.group(1))
            minute = 0

    if hour is None:
        return None, None

    event_date = None

    for d, idx in weekdays.items():
        if d in text:
            days_ahead = idx - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            event_date = (now + timedelta(days=days_ahead)).date()
            break

    if event_date is None:
        if "tomorrow" in text or "morgen" in text:
            event_date = (now + timedelta(days=1)).date()
        elif "overmorgen" in text:
            event_date = (now + timedelta(days=2)).date()
        else:
            event_date = now.date()

    dt = datetime(event_date.year, event_date.month, event_date.day, hour, minute, tzinfo=NL_TZ)

    title = text
    title = re.sub(r"\d{1,2}:\d{2}", "", title)
    title = re.sub(r"\b\d{1,2}\b", "", title)

    for w in weekdays.keys():
        title = title.replace(w, "")

    for w in ["tomorrow", "morgen", "overmorgen"]:
        title = title.replace(w, "")

    title = title.strip()
    if title == "":
        title = "event"

    return title, dt

# ================= DB ================= #

def add_event(chat_id, title, dt):
    cursor.execute(
        "INSERT INTO events (chat_id, title, event_time) VALUES (?, ?, ?)",
        (chat_id, title, dt.isoformat()),
    )
    conn.commit()

def get_events(chat_id):
    cursor.execute("SELECT * FROM events WHERE chat_id=? ORDER BY event_time", (chat_id,))
    return cursor.fetchall()

def delete_event(event_id):
    cursor.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()

def edit_event(event_id, new_title, new_dt):
    cursor.execute(
        "UPDATE events SET title=?, event_time=?, reminded=0 WHERE id=?",
        (new_title, new_dt.isoformat(), event_id),
    )
    conn.commit()

# ================= MENU ================= #

def agenda_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Day", callback_data="day")],
        [InlineKeyboardButton("📆 Week", callback_data="week")],
        [InlineKeyboardButton("🗓 Month", callback_data="month")],
    ])

# ================= COMMANDS ================= #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Calendar bot active")

async def agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📂 Choose view:", reply_markup=agenda_menu())

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    chat_id = q.message.chat.id
    now = datetime.now(NL_TZ)
    events = get_events(chat_id)

    if q.data == "day":
        filtered = [e for e in events if datetime.fromisoformat(e[3]).date() == now.date()]
        title = "📅 Today"

    elif q.data == "week":
        filtered = [e for e in events if now.date() <= datetime.fromisoformat(e[3]).date() <= now.date() + timedelta(days=7)]
        title = "📆 This week"

    else:
        filtered = [e for e in events if datetime.fromisoformat(e[3]).month == now.month]
        title = "🗓 This month"

    msg = f"{title}\n\n"
    for e in filtered:
        dt = datetime.fromisoformat(e[3])
        msg += f"📌 {e[2]} → {dt.strftime('%d-%m %H:%M')}\n"

    await q.message.reply_text(msg if filtered else "No events")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    title, dt = parse(text)

    if not dt:
        await update.message.reply_text("❌ Example: Sunday 19:30 dinner")
        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Added\n📌 {title}\n🕒 {dt.strftime('%A %d %B %H:%M')}"
    )

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        delete_event(int(context.args[0]))
        await update.message.reply_text("🗑 Deleted")
    except:
        await update.message.reply_text("Usage: /delete ID")

async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        event_id = int(context.args[0])
        text = " ".join(context.args[1:])
        title, dt = parse(text)

        if not dt:
            await update.message.reply_text("Invalid format")
            return

        edit_event(event_id, title, dt)
        await update.message.reply_text("✏️ Updated")

    except:
        await update.message.reply_text("Usage: /edit ID new text")

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

                    cursor.execute("UPDATE events SET reminded=1 WHERE id=?", (e[0],))
                    conn.commit()

                except:
                    pass

        time.sleep(30)

# ================= MORNING SUMMARY ================= #

def morning_loop(app):
    sent = set()

    while True:
        now = datetime.now(NL_TZ)

        if now.hour == 8 and now.date() not in sent:
            cursor.execute("SELECT DISTINCT chat_id FROM events")
            chats = cursor.fetchall()

            for c in chats:
                chat_id = c[0]
                events = get_events(chat_id)

                today = [e for e in events if datetime.fromisoformat(e[3]).date() == now.date()]

                if today:
                    msg = "🌅 Today:\n\n"
                    for e in today:
                        dt = datetime.fromisoformat(e[3])
                        msg += f"📌 {e[2]} → {dt.strftime('%H:%M')}\n"

                    try:
                        app.bot.send_message(chat_id=chat_id, text=msg)
                    except:
                        pass

            sent.add(now.date())

        time.sleep(60)

# ================= MAIN ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    app.bot.delete_webhook(drop_pending_updates=True)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("agenda", agenda))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("edit", edit_cmd))

    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    threading.Thread(target=reminder_loop, args=(app,), daemon=True).start()
    threading.Thread(target=morning_loop, args=(app,), daemon=True).start()

    print("Bot running (V2 FULL)")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()
