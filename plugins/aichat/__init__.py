"""Plugin AI chat: /ai <pertanyaan> — tanya AI gratis via Pollinations.ai.

Tanpa API key. Jatah gabung dengan downloader & tools: 5/hari gratis,
premium unlimited. Jatah cuma kepotong kalau AI-nya beneran jawab
(timeout/error nggak makan jatah).
"""

import asyncio
import json
import logging
import urllib.request

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import (
    get_downloads_today,
    inc_downloads_today,
    is_vip,
    upsert_user,
)

log = logging.getLogger("serbabot.aichat")

DAILY_FREE_LIMIT = 5
AI_TIMEOUT = 60  # detik
AI_API = "https://text.pollinations.ai/"
MAX_REPLY = 3500  # Telegram max 4096; potong biar aman

SYSTEM_PROMPT = (
    "Kamu asisten santai berbahasa Indonesia. Jawab pakai bahasa sehari-hari "
    "yang gampang dimengerti, singkat tapi jelas. Jangan pakai bahasa kaku. "
    "Kalau user minta dibuatkan gambar, bilang aja pakai perintah /gambar."
)


def _ask_ai(question):
    """Blocking: tanya Pollinations.ai (POST JSON OpenAI-style), return teks
    jawaban. Dipanggil via to_thread biar event loop nggak ke-block.
    Model 'openai' — model default gratisannya halu parah, yang ini akurat."""
    body = json.dumps(
        {
            "model": "openai",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        AI_API,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace").strip()


async def ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(
            "Pakai: /ai <pertanyaan>\nContoh: /ai kenapa langit warnanya biru?"
        )
        return

    if not premium and get_downloads_today(conn, user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah gratis lu hari ini habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return

    status = await update.message.reply_text("Lagi mikir... 🤔")
    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(_ask_ai, question), timeout=AI_TIMEOUT
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("ai timeout")
        await status.edit_text("AInya kelamaan mikir. Coba lagi nanti 🙏")
        return
    except Exception as e:  # noqa: BLE001 - API mati harus jadi pesan ramah
        log.warning("ai gagal: %s", e)
        await status.edit_text("AInya lagi error. Coba lagi nanti 🙏")
        return

    if not answer:
        await status.edit_text("AInya nggak jawab. Coba tanya yang lain 🙏")
        return
    if len(answer) > MAX_REPLY:
        answer = answer[:MAX_REPLY] + "..."
    if not premium:
        inc_downloads_today(conn, user.id)
    await status.edit_text(answer)


def register(app: Application, db):
    app.add_handler(CommandHandler("ai", ai_cmd))
    log.info("aichat plugin registered")
