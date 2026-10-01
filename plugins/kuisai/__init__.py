"""Plugin Kuis AI: 5 soal kuis dari topik apapun, soalnya dibikin AI.

/kuisai <topik> — soal dibuat Gemini lalu dikirim sebagai poll quiz.
Jawaban benar: +5 koin.
2x/hari gratis, premium unlimited. Butuh GEMINI_API_KEY.
"""
import asyncio
import json
import logging
import os
import re

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
from plugins.aichat import ask_gemini

log = logging.getLogger("serbabot.kuisai")

DAILY_FREE_LIMIT = 2
CORRECT_COINS = 5
QUIZ_COUNT = 5
AI_TIMEOUT = 90  # detik

SYSTEM_TMPL = (
    "Buatkan 5 soal kuis Bahasa Indonesia tentang: {topic}. Jawab HANYA "
    'JSON array: [{{"q": "...", "options": ["a","b","c","d"], "answer": 0}}]'
)


def _parse_quiz(raw):
    """Parse jawaban AI jadi list soal. Raise ValueError kalau format aneh."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("bukan JSON array")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list) or not data:
        raise ValueError("bukan list soal")
    items = []
    for it in data[:QUIZ_COUNT]:
        opts = [str(o) for o in it["options"]]
        ans = int(it["answer"])
        if len(opts) != 4 or not 0 <= ans < 4 or not str(it["q"]).strip():
            raise ValueError("soal tidak valid")
        items.append({"q": str(it["q"]), "options": opts, "answer": ans})
    return items


def register(app: Application, db):
    app.add_handler(CommandHandler("kuisai", kuisai_cmd))
    app.add_handler(PollAnswerHandler(kuisai_answer))
    log.info("kuisai plugin registered")


async def kuisai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text(
            "Pakai: /kuisai <topik>\nContoh: /kuisai sejarah Indonesia"
        )
        return

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        await update.message.reply_text(
            "fitur ini butuh GEMINI_API_KEY, minta admin setting dulu 🙏"
        )
        return

    if not is_vip(conn, user.id, context.bot_data.get("admin_id")) \
            and get_quota_today(conn, "kuisai", user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah kuis AI lu hari ini habis (2/hari). "
            "Balik lagi besok, atau /premium biar unlimited 😎"
        )
        return

    status = await update.message.reply_text("AI lagi bikin soal... 🧠")
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(
                ask_gemini, SYSTEM_TMPL.format(topic=topic), topic, api_key,
                1500,
            ),
            timeout=AI_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 - API mati harus jadi pesan ramah
        log.warning("kuisai gagal: %s", e)
        await status.edit_text("AInya lagi error. Coba lagi nanti 🙏")
        return

    try:
        items = _parse_quiz(raw)
    except (ValueError, KeyError, TypeError):
        await status.edit_text("AI-nya ngasih format aneh, coba topik lain 🙏")
        return

    first = True
    for it in items:
        msg = await update.message.reply_poll(
            it["q"], it["options"],
            type="quiz", correct_option_id=it["answer"], is_anonymous=False,
        )
        context.bot_data.setdefault("kuisai_polls", {})[msg.poll.id] = {
            "user_id": user.id,
            "chat_id": update.effective_chat.id,
            "correct": it["answer"],
        }
        if first:
            # jatah kepotong setelah poll pertama beneran terkirim
            inc_quota_today(conn, "kuisai", user.id)
            first = False
    await update.message.reply_text(
        f"Udah jadi {len(items)} soal tentang \"{topic}\". "
        f"Jawaban bener = +{CORRECT_COINS} koin per soal 🪙"
    )


async def kuisai_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.poll_answer
    conn = context.bot_data["db"]
    info = context.bot_data.get("kuisai_polls", {}).get(ans.poll_id)
    if not info:
        return
    done = context.bot_data.setdefault("kuisai_done", set())
    key = (ans.poll_id, ans.user.id)
    if key in done:  # ganti jawaban / double tap tidak dihitung 2x
        return
    done.add(key)
    if list(ans.option_ids) == [info["correct"]]:
        add_coins(conn, ans.user.id, CORRECT_COINS, "kuis AI benar")
        name = ans.user.first_name or "lu"
        await context.bot.send_message(
            chat_id=info["chat_id"],
            text=f"Mantap {name}! 🔥 Jawaban bener, +{CORRECT_COINS} koin 🪙",
        )
