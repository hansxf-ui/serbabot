"""Plugin referral berjenjang: /invite + credit_referral.

Mekanisme: link invite berisi deep-link /start?start=referral_<user_id>.
Hook di plugins/anonchat.start() memanggil credit_referral() untuk user baru.

Reward:
- L1 (pengundang langsung): +100 koin per teman baru
- L2 (pengundang dari pengundang): +20 koin per teman baru
- Milestone: tepat 5 teman -> 7 hari Premium gratis (sekali per user)
"""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import add_coins, grant_premium_days, upsert_user

log = logging.getLogger("serbabot.referral")

L1_COINS = 100
L2_COINS = 20
MILESTONE_COUNT = 5
MILESTONE_DAYS = 7


def register(app: Application, db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS referrals ("
        "referrer_id INTEGER NOT NULL, "
        "referred_id INTEGER PRIMARY KEY, "
        "created_at TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS referrals_milestone ("
        "user_id INTEGER PRIMARY KEY, "
        "claimed_at TEXT NOT NULL)"
    )
    db.commit()
    app.add_handler(CommandHandler("invite", invite_cmd))
    log.info("referral plugin registered")


def credit_referral(conn, new_user_id, referrer_id):
    """Catat referral user baru + koin L1/L2 + milestone.

    Idempotent: user baru yang sama cuma dihitung sekali. Dipanggil dari
    hook /start (plugins/anonchat); aman terhadap referral diri sendiri.
    """
    if new_user_id == referrer_id:
        return
    already = conn.execute(
        "SELECT 1 FROM referrals WHERE referred_id=?", (new_user_id,)
    ).fetchone()
    if already:
        return

    conn.execute(
        "INSERT INTO referrals(referrer_id, referred_id, created_at) "
        "VALUES(?,?,datetime('now'))",
        (referrer_id, new_user_id),
    )
    add_coins(conn, referrer_id, L1_COINS, "referral teman")

    # L2: siapa yang mereferensikan si referrer
    l2 = conn.execute(
        "SELECT referrer_id FROM referrals WHERE referred_id=?", (referrer_id,)
    ).fetchone()
    if l2:
        add_coins(conn, l2[0], L2_COINS, "referral level 2")

    # Milestone: tepat N teman, sekali klaim
    count = conn.execute(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (referrer_id,)
    ).fetchone()[0]
    claimed = conn.execute(
        "SELECT 1 FROM referrals_milestone WHERE user_id=?", (referrer_id,)
    ).fetchone()
    if count == MILESTONE_COUNT and not claimed:
        # grant_premium_days butuh baris user (UPDATE): pastikan ada.
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, username, created_at) "
            "VALUES(?,NULL,datetime('now'))",
            (referrer_id,),
        )
        grant_premium_days(conn, referrer_id, MILESTONE_DAYS)
        conn.execute(
            "INSERT INTO referrals_milestone(user_id, claimed_at) "
            "VALUES(?,datetime('now'))",
            (referrer_id,),
        )
    conn.commit()


async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=referral_{user.id}"

    l1 = conn.execute(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,)
    ).fetchone()[0]
    total_coins = conn.execute(
        "SELECT COALESCE(SUM(delta), 0) FROM coin_tx "
        "WHERE user_id=? AND reason LIKE 'referral%'",
        (user.id,),
    ).fetchone()[0]

    lines = [
        "🎁 Undang Teman, Dapat Koin!",
        "",
        f"Link invite lu:\n{link}",
        "",
        f"👥 Teman yang udah join: {l1}",
        f"🪙 Total koin dari referral: {total_coins}",
    ]
    if l1 >= MILESTONE_COUNT:
        lines.append(f"🏆 Milestone {MILESTONE_COUNT} teman: udah diklaim. Mantap! 🔥")
    else:
        lines.append(
            f"🏆 Ajak {l1}/{MILESTONE_COUNT} teman → "
            f"{MILESTONE_DAYS} hari Premium gratis!"
        )
    await update.message.reply_text("\n".join(lines))
