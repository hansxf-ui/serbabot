"""Plugin Koin Serba: dompet koin virtual + daily check-in & streak.

/checkin  — check-in harian, dapat koin (bonus streak hari 7/14/30)
/koin     — lihat saldo + 5 riwayat terakhir

Koin dipakai fitur lain (tebak skor, gacha, fotofun, referral). Tanpa limit.
"""
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import (
    CHECKIN_LADDER,
    checkin,
    get_coin_history,
    get_coins,
    get_streak,
    upsert_user,
)

log = logging.getLogger("serbabot.koin")


async def checkin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    res = checkin(conn, user.id)
    if res["already"]:
        await update.message.reply_text(
            f"Lu udah check-in hari ini ✅\n"
            f"Streak: {res['streak']} hari 🔥\n"
            f"Balik lagi besok biar streak-nya nggak putus!"
        )
        return

    streak, bonus = res["streak"], res["bonus"]
    msg = (
        f"✅ Check-in hari ke-{streak}!\n"
        f"+{bonus} koin 🪙 (saldo: {get_coins(conn, user.id)})\n"
    )
    if streak in CHECKIN_LADDER:
        msg += f"🎉 Bonus streak hari ke-{streak}! Mantap 🔥\n"
    nxt = streak + 1
    if nxt in CHECKIN_LADDER:
        msg += f"Besok hari ke-{nxt} = bonus {CHECKIN_LADDER[nxt]} koin. Jangan bolong!"
    else:
        msg += "Check-in tiap hari biar streak naik terus."
    await update.message.reply_text(msg)


async def koin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    saldo = get_coins(conn, user.id)
    streak, _ = get_streak(conn, user.id)
    lines = [
        f"🪙 Koin Serba: {saldo}",
        f"🔥 Streak check-in: {streak} hari (/checkin)",
    ]
    hist = get_coin_history(conn, user.id, limit=5)
    if hist:
        lines.append("\nTerakhir:")
        for delta, reason, _ in hist:
            tanda = "+" if delta > 0 else ""
            lines.append(f"  {tanda}{delta} — {reason}")
    else:
        lines.append("\nBelum ada riwayat. /checkin tiap hari buat kumpulin koin!")
    await update.message.reply_text("\n".join(lines))


def register(app: Application, db):
    app.add_handler(CommandHandler("checkin", checkin_cmd))
    app.add_handler(CommandHandler("koin", koin_cmd))
    log.info("koin plugin registered")
