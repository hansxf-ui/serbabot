"""Plugin Kuis Harian: kuis Bahasa Indonesia + leaderboard.

/kuis — 1 soal quiz acak (3x/hari gratis, premium unlimited)
/top  — leaderboard harian + 7 hari terakhir
Jawaban benar: +1 skor & +5 koin.
"""
import datetime
import json
import logging
import os
import random

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
)

from db import (
    add_coins,
    get_quota_today,
    inc_quota_today,
    is_vip,
    upsert_user,
)

log = logging.getLogger("serbabot.kuis")

DAILY_FREE_LIMIT = 3
CORRECT_COINS = 5
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soal.json")

with open(_DATA, encoding="utf-8") as f:
    SOAL = json.load(f)


def _today():
    return datetime.date.today().isoformat()


def register(app: Application, db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS kuis_scores("
        "user_id INTEGER NOT NULL, date TEXT NOT NULL, "
        "score INTEGER NOT NULL DEFAULT 0, "
        "answered INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY(user_id, date))"
    )
    db.commit()
    app.add_handler(CommandHandler("kuis", kuis_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(PollAnswerHandler(kuis_answer))
    log.info("kuis plugin registered")


async def kuis_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    if not is_vip(conn, user.id, context.bot_data.get("admin_id")) \
            and get_quota_today(conn, "kuis", user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah kuis gratis lu hari ini habis (3/hari). "
            "Balik lagi besok, atau /premium biar unlimited 😎"
        )
        return
    soal = random.choice(SOAL)
    msg = await update.message.reply_poll(
        soal["q"], soal["options"],
        type="quiz", correct_option_id=soal["answer"], is_anonymous=False,
    )
    context.bot_data.setdefault("kuis_polls", {})[msg.poll.id] = {
        "user_id": user.id,
        "chat_id": update.effective_chat.id,
        "correct": soal["answer"],
    }
    inc_quota_today(conn, "kuis", user.id)


async def kuis_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.poll_answer
    conn = context.bot_data["db"]
    info = context.bot_data.get("kuis_polls", {}).get(ans.poll_id)
    if not info:
        return
    done = context.bot_data.setdefault("kuis_done", set())
    key = (ans.poll_id, ans.user.id)
    if key in done:  # ganti jawaban / double tap tidak dihitung 2x
        return
    done.add(key)
    today = _today()
    conn.execute(
        "INSERT INTO kuis_scores(user_id, date, score, answered)"
        " VALUES(?,?,0,0) ON CONFLICT(user_id, date) DO NOTHING",
        (ans.user.id, today),
    )
    if list(ans.option_ids) == [info["correct"]]:
        conn.execute(
            "UPDATE kuis_scores SET score=score+1, answered=answered+1"
            " WHERE user_id=? AND date=?",
            (ans.user.id, today),
        )
        conn.commit()
        add_coins(conn, ans.user.id, CORRECT_COINS, "kuis benar")
        name = ans.user.first_name or "lu"
        await context.bot.send_message(
            chat_id=info["chat_id"],
            text=f"Mantap {name}! 🔥 Jawaban bener, +{CORRECT_COINS} koin 🪙",
        )
    else:
        conn.execute(
            "UPDATE kuis_scores SET answered=answered+1"
            " WHERE user_id=? AND date=?",
            (ans.user.id, today),
        )
        conn.commit()


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    today = _today()
    week_ago = (datetime.date.today() - datetime.timedelta(days=6)).isoformat()
    lines = ["🏆 Leaderboard Kuis\n", "📅 Hari ini:"]
    rows = conn.execute(
        "SELECT u.username, s.score, s.answered FROM kuis_scores s"
        " LEFT JOIN users u ON u.user_id=s.user_id"
        " WHERE s.date=? ORDER BY s.score DESC, s.answered ASC LIMIT 10",
        (today,),
    ).fetchall()
    if rows:
        for i, (uname, score, answered) in enumerate(rows, 1):
            lines.append(f"{i}. @{uname or '?'} — {score} bener / {answered} soal")
    else:
        lines.append("(belum ada yang main hari ini, /kuis gih!)")
    lines.append("\n📊 7 hari terakhir:")
    rows = conn.execute(
        "SELECT u.username, SUM(s.score), SUM(s.answered) FROM kuis_scores s"
        " LEFT JOIN users u ON u.user_id=s.user_id"
        " WHERE s.date>=? GROUP BY s.user_id"
        " ORDER BY 2 DESC LIMIT 10",
        (week_ago,),
    ).fetchall()
    if rows:
        for i, (uname, score, answered) in enumerate(rows, 1):
            lines.append(f"{i}. @{uname or '?'} — {score} bener / {answered} soal")
    else:
        lines.append("(masih kosong)")
    await update.message.reply_text("\n".join(lines))
