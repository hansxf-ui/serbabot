"""Plugin Menfess Anonim: /menfess <teks> -> dipost ke channel secara anonim.

Jatah: 3/hari gratis, VIP unlimited. Jatah cuma kepotong kalau menfess
beneran terkirim ke channel.
"""

import logging
import os

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
from plugins.anonchat.filtering import contains_badword, load_badwords

log = logging.getLogger("serbabot.menfess")

MENFESS_LIMIT = 3
BADWORDS = load_badwords()


def register(app: Application, db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS menfess ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "sender_id INTEGER NOT NULL, "
        "text TEXT NOT NULL, "
        "counter INTEGER NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    db.commit()
    app.add_handler(CommandHandler("menfess", menfess_cmd))
    log.info("menfess plugin registered")


def _next_counter(conn):
    row = conn.execute("SELECT COALESCE(MAX(counter), 0) FROM menfess").fetchone()
    return row[0] + 1


async def menfess_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    teks = " ".join(context.args).strip()
    if not teks:
        await update.message.reply_text(
            "Pakai: /menfess <teks>\n"
            "Contoh: /menfess ada yang jual sepeda bekas nggak?"
        )
        return

    if contains_badword(teks, BADWORDS):
        await update.message.reply_text(
            "Menfess lu kena filter kata kasar. Edit bahasanya dulu, "
            "baru kirim lagi."
        )
        return

    channel_id = os.environ.get("MENFESS_CHANNEL_ID", "").strip()
    if not channel_id:
        await update.message.reply_text(
            "Menfess belum disetting admin. "
            "Minta admin set MENFESS_CHANNEL_ID dulu 🙏"
        )
        return

    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))
    if not premium and get_quota_today(conn, "menfess", user.id) >= MENFESS_LIMIT:
        await update.message.reply_text(
            "Jatah menfess gratis lu hari ini habis (3/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return

    nomor = _next_counter(conn)
    try:
        await context.bot.send_message(
            chat_id=channel_id, text=f"📮 Menfess #{nomor}\n\n{teks}"
        )
    except Exception as e:  # noqa: BLE001 - kirim gagal = jujur, bukan fake kirim
        log.warning("gagal kirim menfess ke channel: %s", e)
        await update.message.reply_text(
            "Gagal ngirim ke channel. Coba lagi nanti, atau kabarin admin 🙏"
        )
        return

    conn.execute(
        "INSERT INTO menfess(sender_id, text, counter, created_at) "
        "VALUES(?,?,?,datetime('now'))",
        (user.id, teks, nomor),
    )
    conn.commit()
    if not premium:
        inc_quota_today(conn, "menfess", user.id)
    await update.message.reply_text(
        f"Menfess #{nomor} terkirim! 📮\n"
        "Anonim 100%, nggak ada yang tau siapa pengirimnya."
    )
