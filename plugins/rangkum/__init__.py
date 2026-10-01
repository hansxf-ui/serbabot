"""Plugin rangkum: /rangkum <url> — fetch artikel lalu ringkas pakai AI.

Butuh GEMINI_API_KEY. Tanpa key -> pesan jujur (bukan fake).

Jatah: 5/hari (feature "rangkum"), VIP unlimited. Jatah cuma kepotong
kalau ringkasan beneran terkirim (fetch gagal / AI error -> jatah aman).
"""
import asyncio
import html
import logging
import os
import re
import urllib.error
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
from plugins.aichat import ask_gemini

log = logging.getLogger("serbabot.rangkum")

DAILY_FREE_LIMIT = 5
FETCH_TIMEOUT = 15  # detik
MAX_CHARS = 8000  # batas teks yang dikirim ke AI
MIN_CHARS = 200  # konten lebih pendek dari ini dianggap gagal
MAX_REPLY = 3500  # Telegram max 4096; potong biar aman

SUMMARIZE_PROMPT = (
    "Ringkas teks berikut jadi 5 poin penting Bahasa Indonesia "
    "yang gampang dipahami."
)


def fetch_article(url):
    """Blocking: fetch URL, balikin judul + teks dari <p> (HTML dibuang).

    Raise kalau bukan URL valid, fetch gagal, atau konten < 200 char.
    Dipanggil via to_thread biar event loop nggak ke-block.
    """
    if not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        raise ValueError("URL-nya nggak valid. Contoh: /rangkum https://contoh.com/berita")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Gagal fetch URL-nya ({e.reason}). Coba lagi nanti 🙏")
    except Exception as e:  # noqa: BLE001 - timeout dll jadi pesan ramah
        raise RuntimeError(f"Gagal fetch URL-nya ({e}). Coba lagi nanti 🙏")

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()

    body = raw
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body,
                  flags=re.IGNORECASE | re.DOTALL)
    paras = re.findall(r"<p[^>]*>(.*?)</p>", body, re.IGNORECASE | re.DOTALL)
    text = " ".join(
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip()
        for p in paras
    )
    text = re.sub(r"\s+", " ", text).strip()
    if title:
        text = f"Judul: {title}\n{text}"
    if len(text) < MIN_CHARS:
        raise RuntimeError(
            "Kontennya terlalu pendek buat dirangkum. Coba URL artikel lain 🙏")
    return text[:MAX_CHARS]


async def rangkum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        await update.message.reply_text(
            "Rangkum butuh GEMINI_API_KEY, minta admin setting 🙏")
        return

    url = " ".join(context.args).strip()
    if not url:
        await update.message.reply_text(
            "Pakai: /rangkum <url>\nContoh: /rangkum https://contoh.com/berita")
        return

    if not premium and get_quota_today(conn, "rangkum", user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah rangkum lu hari ini habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium")
        return

    status = await update.message.reply_text("Lagi baca artikelnya... 📖")
    try:
        text = await asyncio.to_thread(fetch_article, url)
    except (ValueError, RuntimeError) as e:
        await status.edit_text(str(e))
        return

    await status.edit_text("Lagi merangkum... 📝")
    try:
        summary = await asyncio.wait_for(
            asyncio.to_thread(
                ask_gemini, SUMMARIZE_PROMPT, text, api_key, 800),
            timeout=90,
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("rangkum timeout")
        await status.edit_text("AInya kelamaan mikir. Coba lagi nanti 🙏")
        return
    except Exception as e:  # noqa: BLE001 - AI mati harus jadi pesan ramah
        log.warning("rangkum gagal: %s", e)
        await status.edit_text("AInya lagi error. Coba lagi nanti 🙏")
        return

    if len(summary) > MAX_REPLY:
        summary = summary[:MAX_REPLY] + "..."
    if not premium:
        inc_quota_today(conn, "rangkum", user.id)
    await status.edit_text(summary)


def register(app: Application, db):
    app.add_handler(CommandHandler("rangkum", rangkum_cmd))
    log.info("rangkum plugin registered")
