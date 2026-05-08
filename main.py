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

# ================= SMART PARSER ================= #

def parse(text: str):

    text = text.lower().strip()

    now = datetime.now(NL_TZ)

    # ================= MONTHS ================= #

    months = {
        "januari": 1,
        "februari": 2,
        "maart": 3,
        "april": 4,
        "mei": 5,
        "juni": 6,
        "juli": 7,
        "augustus": 8,
        "september": 9,
        "oktober": 10,
        "november": 11,
        "december": 12,

        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    # ================= WEEKDAYS ================= #

    weekdays = {
        "maandag": 0,
        "dinsdag": 1,
        "woensdag": 2,
        "donderdag": 3,
        "vrijdag": 4,
        "zaterdag": 5,
        "zondag": 6,

        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    # ================= TIME ================= #

    hour = None
    minute = 0

    # 18:30
    match = re.search(r"(\d{1,2}):(\d{2})", text)

    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))

    # 3 uur
    if hour is None:

        match = re.search(r"(\d{1,2})\s*uur", text)

        if match:
            hour = int(match.group(1))
            minute = 0

    # half 3
    if hour is None:

        match = re.search(r"half\s+(\d{1,2})", text)

        if match:
            hour = int(match.group(1)) - 1
            minute = 30

    # kwart voor 4
    if hour is None:

        match = re.search(r"kwart\s+voor\s+(\d{1,2})", text)

        if match:
            hour = int(match.group(1)) - 1
            minute = 45

    # kwart over 5
    if hour is None:

        match = re.search(r"kwart\s+over\s+(\d{1,2})", text)

        if match:
            hour = int(match.group(1))
            minute = 15

    # middag / avond
    if "middag" in text and hour is not None:
        if hour < 12:
            hour += 12

    if "avond" in text and hour is not None:
        if hour < 12:
            hour += 12

    # Geen tijd
    if hour is None:
        return None, None

    # ================= DATE ================= #

    event_date = now.date()

    # morgen
    if "morgen" in text or "tomorrow" in text:
        event_date = (now + timedelta(days=1)).date()

    # overmorgen
    elif "overmorgen" in text:
        event_date = (now + timedelta(days=2)).date()

    # weekdays
    else:

        found_weekday = False

        for day, weekday_num in weekdays.items():

            if day in text:

                current_weekday = now.weekday()

                days_ahead = weekday_num - current_weekday

                if days_ahead <= 0:
                    days_ahead += 7

                event_date = (
                    now + timedelta(days=days_ahead)
                ).date()

                found_weekday = True

                break

        # 12 mei
        if not found_weekday:

            for month_name, month_num in months.items():

                pattern = rf"(\d{{1,2}})\s+{month_name}"

                match = re.search(pattern, text)

                if match:

                    day_num = int(match.group(1))

                    year = now.year

                    try:

                        possible_date = datetime(
                            year,
                            month_num,
                            day_num
                        ).date()

                        # volgend jaar als datum al voorbij is
                        if possible_date < now.date():
                            possible_date = datetime(
                                year + 1,
                                month_num,
                                day_num
                            ).date()

                        event_date = possible_date

                    except:
                        pass

                    break

    # ================= DATETIME ================= #

    dt = datetime(
        year=event_date.year,
        month=event_date.month,
        day=event_date.day,
        hour=hour,
        minute=minute,
        tzinfo=NL_TZ
    )

    # ================= TITLE CLEANUP ================= #

    title = text

    patterns = [
        r"\d{1,2}:\d{2}",
        r"\d{1,2}\s*uur",
        r"half\s+\d{1,2}",
        r"kwart\s+voor\s+\d{1,2}",
        r"kwart\s+over\s+\d{1,2}",
    ]

    for p in patterns:
        title = re.sub(p, "", title)

    remove_words = [
        "morgen",
        "overmorgen",
        "today",
        "tomorrow",
        "middag",
        "avond",

        "maandag",
        "dinsdag",
        "woensdag",
        "donderdag",
        "vrijdag",
        "zaterdag",
        "zondag",

        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]

    for w in remove_words:
        title = title.replace(w, "")

    # maandnamen verwijderen
    for month_name in months.keys():
        title = title.replace(month_name, "")

    # losse nummers verwijderen
    title = re.sub(r"\b\d{1,2}\b", "", title)

    title = re.sub(r"\s+", " ", title).strip()

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

    title, dt = parse(text)

    if not dt:

        await update.message.reply_text(
            "❌ Voorbeelden:\n\n"
            "morgen 3 uur tandarts\n"
            "vrijdag lunch 13:00\n"
            "12 mei vakantie 08:00\n"
            "overmorgen gym 18:00"
        )

        return

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Toegevoegd:\n"
        f"{title}\n"
        f"🕒 {dt.strftime('%d-%m %Y %H:%M')}"
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("agenda", agenda))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("delete", delete_cmd))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

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
