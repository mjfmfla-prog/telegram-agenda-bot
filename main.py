import os
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG ================= #

TOKEN = os.getenv("TOKEN")
RENDER_URL = os.getenv("RENDER_URL")
PORT = int(os.environ.get("PORT", 10000))
TZ = ZoneInfo("Europe/Amsterdam")

# ================= DATABASE ================= #

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

# ================= DATA ================= #

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "maandag": 0,
    "dinsdag": 1,
    "woensdag": 2,
    "donderdag": 3,
    "vrijdag": 4,
    "zaterdag": 5,
    "zondag": 6,
}

MONTHS = {
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
    "mei": 5,
    "juni": 6,
    "juli": 7,
}

# ================= EVENT FILTER ================= #

def is_event(text: str) -> bool:
    t = text.lower().strip()

    if len(t.split()) < 2:
        return False

    has_time = bool(re.search(r"\b\d{1,2}(:\d{2})?\b", t))
    has_weekday = any(d in t for d in WEEKDAYS)
    has_date = bool(re.search(r"\b\d{1,2}\s+[a-z]+\b", t))
    has_relative = (
        "tomorrow" in t or
        "morgen" in t or
        "overmorgen" in t
    )

    score = 0

    if has_time:
        score += 1

    if has_weekday:
        score += 1

    if has_date:
        score += 1

    if has_relative:
        score += 1

    if len(t.split()) >= 3:
        score += 1

    return score >= 2

# ================= CLEAN TITLE ================= #

def clean_title(raw: str):
    t = raw.lower()

    for d in WEEKDAYS:
        t = re.sub(rf"\b{d}\b", "", t)

    for m in MONTHS:
        t = re.sub(rf"\b{m}\b", "", t)

    t = t.replace("tomorrow", "")
    t = t.replace("morgen", "")
    t = t.replace("overmorgen", "")

    t = re.sub(r"\d{1,2}:\d{2}", "", t)
    t = re.sub(r"\b\d{1,2}\b", "", t)

    t = re.sub(r"\s+", " ", t).strip()

    return t.capitalize() if t else "Event"

# ================= TIME PARSER ================= #

def parse_time(raw):
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", raw)

    if m:
        return int(m.group(1)), int(m.group(2))

    m = re.search(r"\b(\d{1,2})\b", raw)

    if m:
        return int(m.group(1)), 0

    return 9, 0

# ================= PARSER ================= #

def parse(text: str):
    raw = text.lower().strip()
    now = datetime.now(TZ)

    hour, minute = parse_time(raw)

    event_date = None

    # tomorrow
    if "tomorrow" in raw or "morgen" in raw:
        event_date = (now + timedelta(days=1)).date()

    # overmorgen
    if "overmorgen" in raw:
        event_date = (now + timedelta(days=2)).date()

    # explicit date
    date_match = re.search(r"(\d{1,2})\s+([a-z]+)", raw)

    if date_match:
        day = int(date_match.group(1))
        month_name = date_match.group(2)
        month = MONTHS.get(month_name)

        if month:
            try:
                event_date = datetime(now.year, month, day).date()
            except:
                pass

    # weekday
    if event_date is None:
        for d, idx in WEEKDAYS.items():
            if d in raw:
                diff = idx - now.weekday()

                if diff <= 0:
                    diff += 7

                event_date = (now + timedelta(days=diff)).date()
                break

    # default today
    if event_date is None:
        event_date = now.date()

    dt = datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        hour,
        minute,
        tzinfo=TZ,
    )

    return clean_title(raw), dt

# ================= DATABASE FUNCTIONS ================= #

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


def edit_event(event_id, title, dt):
    cursor.execute(
        "UPDATE events SET title=?, event_time=? WHERE id=?",
        (title, dt.isoformat(), event_id),
    )
    conn.commit()

# ================= FORMATTER ================= #

def format_view(title, events):
    grouped = {}

    for e in events:
        dt = datetime.fromisoformat(e[3])
        grouped.setdefault(dt.date(), []).append((e, dt))

    msg = f"{title}\n\n"

    for day, items in sorted(grouped.items()):
        msg += "━━━━━━━━━━━━━━\n"
        msg += f"📅 {day.strftime('%A %d %B')}\n"
        msg += "━━━━━━━━━━━━━━\n"

        for e, dt in items:
            msg += f"🕒 {dt.strftime('%H:%M')} {e[2]} (ID {e[0]})\n"

        msg += "\n"

    return msg

# ================= HANDLER ================= #

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if not is_event(text):
        return

    title, dt = parse(text)

    add_event(update.effective_chat.id, title, dt)

    await update.message.reply_text(
        f"✅ Added\n📌 {title}\n🕒 {dt.strftime('%A %d %B %H:%M')}"
    )

# ================= DAY ================= #

async def day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    events = get_events(update.effective_chat.id)

    filtered = [
        e for e in events
        if datetime.fromisoformat(e[3]).date() == now.date()
    ]

    await update.message.reply_text(
        format_view("📅 Today", filtered)
        if filtered else "No events"
    )

# ================= WEEK ================= #

async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)

    start = now.date() - timedelta(days=now.weekday())
    end = start + timedelta(days=6)

    events = get_events(update.effective_chat.id)

    filtered = [
        e for e in events
        if start <= datetime.fromisoformat(e[3]).date() <= end
    ]

    await update.message.reply_text(
        format_view(
            f"📆 Week ({start.strftime('%d %b')} - {end.strftime('%d %b')})",
            filtered,
        ) if filtered else "No events"
    )

# ================= NEXT WEEK ================= #

async def nextweek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)

    start = now.date() - timedelta(days=now.weekday()) + timedelta(days=7)
    end = start + timedelta(days=6)

    events = get_events(update.effective_chat.id)

    filtered = [
        e for e in events
        if start <= datetime.fromisoformat(e[3]).date() <= end
    ]

    await update.message.reply_text(
        format_view(
            f"📆 Next Week ({start.strftime('%d %b')} - {end.strftime('%d %b')})",
            filtered,
        ) if filtered else "No events"
    )

# ================= MONTH ================= #

async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)

    events = get_events(update.effective_chat.id)

    filtered = [
        e for e in events
        if datetime.fromisoformat(e[3]).month == now.month
        and datetime.fromisoformat(e[3]).year == now.year
    ]

    await update.message.reply_text(
        format_view(
            f"🗓 Month ({now.strftime('%B %Y')})",
            filtered,
        ) if filtered else "No events"
    )

# ================= DELETE ================= #

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /delete <id>")
        return

    delete_event(int(context.args[0]))

    await update.message.reply_text("🗑 Deleted")

# ================= EDIT ================= #

async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /edit <id> text")
        return

    event_id = int(context.args[0])
    text = " ".join(context.args[1:])

    title, dt = parse(text)

    edit_event(event_id, title, dt)

    await update.message.reply_text("✏️ Updated")

# ================= MAIN ================= #

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("nextweek", nextweek))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("edit", edit))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle)
    )

    print("Calendar bot v8 running")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}",
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
