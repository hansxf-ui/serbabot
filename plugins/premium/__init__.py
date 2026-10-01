"""Plugin /premium — info upgrade premium.

Pendaftaran premium belum dibuka (sistem pembayaran manual lagi disiapin).
Perintah ini ada biar pesan "upgrade ke Premium: /premium" nggak jadi
dead end.
"""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

log = logging.getLogger("serbabot.premium")


async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⭐ Premium SerbaBot\n\n"
        "Pendaftarannya lagi disiapin, bentar lagi dibuka 🙏\n"
        "Nanti: bayar sekali sebulan, jatah harian jadi unlimited."
    )


def register(app: Application, db):
    app.add_handler(CommandHandler("premium", premium_cmd))
    log.info("premium plugin registered")
