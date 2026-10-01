"""Plugin sholat: jadwal sholat + pengingat otomatis.

/sholat <kota>    — jadwal hari ini (metode KEMENAG via api.aladhan.com)
/sholatset <kota> — langganan: tiap hari diingetin 5 menit sebelum tiap waktu
/sholatstop       — berhenti langganan
"""
import asyncio
import datetime
import json
import logging
import urllib.parse
import urllib.request

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import (
    get_quota_today,
    inc_quota_today,
    is_vip,
    upsert_user,
)

log = logging.getLogger("serbabot.sholat")

SHOLAT_LIMIT = 20  # per hari
SHOLAT_TIMEOUT = 15  # detik
API_URL = "https://api.aladhan.com/v1/timingsByCity"
API_METHOD = 20  # KEMENAG Indonesia
WIB = datetime.timezone(datetime.timedelta(hours=7))

# (nama di API aladhan, nama tampilan)
WAKTU = [
    ("Fajr", "Subuh"),
    ("Dhuhr", "Dzuhur"),
    ("Asr", "Ashar"),
    ("Maghrib", "Maghrib"),
    ("Isha", "Isya"),
]

SUBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sholat_subs (
    user_id INTEGER PRIMARY KEY,
    city TEXT NOT NULL
);
"""


def parse_timings(payload):
    """payload (dict hasil json.loads dari aladhan) -> dict
    {Subuh, Dzuhur, Ashar, Maghrib, Isya: 'HH:MM', hijri: '...'|None}."""
    data = payload.get("data") or {}
    timings = data.get("timings") or {}
    out = {}
    for api_name, nama in WAKTU:
        raw = timings.get(api_name)
        out[nama] = raw.split()[0] if raw else "-"
    hijri = (data.get("date") or {}).get("hijri") or {}
    day = hijri.get("day")
    month = (hijri.get("month") or {}).get("en")
    year = hijri.get("year")
    out["hijri"] = f"{day} {month} {year} H" if day and month and year else None
    return out


def next_reminders(timings, now=None):
    """[(nama, datetime_pengingat)] — 5 menit sebelum tiap waktu yang
    masih di masa depan hari ini."""
    now = now or datetime.datetime.now()
    out = []
    for _, nama in WAKTU:
        hhmm = timings.get(nama)
        if not hhmm or hhmm == "-":
            continue
        h, m = map(int, hhmm.split()[0].split(":"))
        waktu = now.replace(hour=h, minute=m, second=0, microsecond=0)
        ingat = waktu - datetime.timedelta(minutes=5)
        if ingat > now:
            out.append((nama, ingat))
    return out


def _fetch_timings(city):
    """Blocking: GET aladhan timingsByCity. Return dict parse_timings.
    Dipanggil via to_thread biar event loop nggak ke-block."""
    qs = urllib.parse.urlencode(
        {"city": city, "country": "Indonesia", "method": API_METHOD}
    )
    req = urllib.request.Request(f"{API_URL}?{qs}", method="GET")
    with urllib.request.urlopen(req, timeout=SHOLAT_TIMEOUT) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))
    return parse_timings(payload)


def _format_timings(city, timings):
    lines = [f"🕌 Jadwal Sholat — {city}"]
    if timings.get("hijri"):
        lines.append(f"📅 {timings['hijri']}")
    lines.append("")
    for _, nama in WAKTU:
        lines.append(f"{nama:<8} {timings.get(nama, '-')}")
    return "\n".join(lines)


def _schedule_daily(job_queue, user_id, city):
    """(Ulang-)jadwalkan job harian 00:05 WIB yang nyiapin pengingat hari itu."""
    name = f"sholat_daily_{user_id}"
    for job in job_queue.get_jobs_by_name(name):
        job.schedule_removal()
    job_queue.run_daily(
        _daily_job,
        time=datetime.time(0, 5, tzinfo=WIB),
        data={"user_id": user_id, "city": city},
        name=name,
    )


async def sholat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    city = " ".join(context.args).strip()
    if not city:
        await update.message.reply_text(
            "Pakai: /sholat <kota>\nContoh: /sholat Jakarta"
        )
        return
    if not premium and get_quota_today(conn, "sholat", user.id) >= SHOLAT_LIMIT:
        await update.message.reply_text(
            f"Jatah /sholat lu hari ini habis ({SHOLAT_LIMIT}/hari). "
            "Besok lagi ya, atau upgrade Premium biar unlimited."
        )
        return

    status = await update.message.reply_text(f"Lagi ambil jadwal sholat {city}... 🕌")
    try:
        timings = await asyncio.to_thread(_fetch_timings, city)
    except Exception as e:  # noqa: BLE001 - API mati harus jadi pesan ramah
        log.warning("sholat gagal buat %s: %s", city, e)
        await status.edit_text("Nggak bisa ambil jadwal, coba lagi nanti 🙏")
        return

    if not premium:
        inc_quota_today(conn, "sholat", user.id)
    await status.edit_text(_format_timings(city, timings))


async def sholatset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    city = " ".join(context.args).strip()
    if not city:
        await update.message.reply_text(
            "Pakai: /sholatset <kota>\nContoh: /sholatset Bandung\n"
            "Nanti diingetin tiap 5 menit sebelum waktu sholat."
        )
        return
    conn.execute(
        "INSERT INTO sholat_subs(user_id, city) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET city=excluded.city",
        (user.id, city),
    )
    conn.commit()
    _schedule_daily(context.job_queue, user.id, city)
    await update.message.reply_text(
        f"Siap! Tiap hari bakal diingetin 5 menit sebelum waktu sholat "
        f"(kota: {city}) 🕌\nBerhenti kapan aja: /sholatstop"
    )


async def sholatstop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    conn.execute("DELETE FROM sholat_subs WHERE user_id=?", (user.id,))
    conn.commit()
    for job in context.job_queue.get_jobs_by_name(f"sholat_daily_{user.id}"):
        job.schedule_removal()
    await update.message.reply_text("Pengingat sholat dimatiin. Sampai jumpa lagi! 🕌")


async def _daily_job(context: ContextTypes.DEFAULT_TYPE):
    """Job harian 00:05: fetch jadwal hari ini, lalu schedule 5 pengingat."""
    data = context.job.data
    user_id, city = data["user_id"], data["city"]
    try:
        timings = await asyncio.to_thread(_fetch_timings, city)
    except Exception as e:  # noqa: BLE001 - API mati = skip hari ini
        log.warning("sholat daily gagal buat %s: %s", city, e)
        return
    for nama, kapan in next_reminders(timings):
        context.job_queue.run_once(
            _remind_job,
            when=kapan,
            data={"chat_id": user_id, "nama": nama},
            name=f"sholat_rem_{user_id}_{nama}",
        )
    log.info("sholat: pengingat dijadwalkan buat %s (%s)", user_id, city)


async def _remind_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    await context.bot.send_message(
        chat_id=data["chat_id"],
        text=f"⏰ 5 menit lagi {data['nama']} 🕌\nSiap-siap sholat ya!",
    )


def register(app: Application, db):
    db.execute(SUBS_SCHEMA)
    db.commit()
    # Restore langganan setelah restart: job PTB itu in-memory, jadi
    # jadwalkan ulang dari tabel sholat_subs yang persisten.
    try:
        for user_id, city in db.execute("SELECT user_id, city FROM sholat_subs"):
            _schedule_daily(app.job_queue, user_id, city)
    except Exception as e:  # noqa: BLE001 - restore gagal = log saja
        log.warning("sholat: restore langganan gagal: %s", e)
    app.add_handler(CommandHandler("sholat", sholat_cmd))
    app.add_handler(CommandHandler("sholatset", sholatset_cmd))
    app.add_handler(CommandHandler("sholatstop", sholatstop_cmd))
    log.info("sholat plugin registered")
