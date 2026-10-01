"""Plugin /gambar — generate gambar AI gratis via Pollinations.ai.

Tanpa API key. Contoh: /gambar kucing astronot di bulan
Jatah gabung dengan downloader/tools/ai: 5/hari gratis, admin & premium
unlimited. Jatah cuma kepotong kalau gambar beneran kekirim.
"""

import asyncio
import logging
import os
import tempfile
import urllib.parse
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

log = logging.getLogger("serbabot.imagegen")

DAILY_FREE_LIMIT = 5
IMG_TIMEOUT = 120  # detik — generate gambar bisa lama
IMG_API = "https://image.pollinations.ai/prompt/"


def _download_image(prompt):
    """Blocking: download hasil generate ke file temp, return path.
    Dipanggil via to_thread biar event loop nggak ke-block."""
    url = (
        IMG_API
        + urllib.parse.quote(prompt)
        + "?width=1024&height=1024&nologo=true&model=flux"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "SerbaBot/1.0"})
    with urllib.request.urlopen(req, timeout=IMG_TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        data = r.read()
    if len(data) < 1024:
        raise RuntimeError("gambar kosong/kegagalan")
    fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="serbaimg_")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return tmp


async def gambar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text(
            "Pakai: /gambar <deskripsi>\n"
            "Contoh: /gambar kucing astronot di bulan"
        )
        return

    if not premium and get_downloads_today(conn, user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah gratis lu hari ini habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return

    status = await update.message.reply_text("Lagi gambar... 🎨")
    try:
        tmp = await asyncio.wait_for(
            asyncio.to_thread(_download_image, prompt), timeout=IMG_TIMEOUT
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("gambar timeout")
        await status.edit_text("Gambarnya kelamaan. Coba lagi nanti 🙏")
        return
    except Exception as e:  # noqa: BLE001 - API mati harus jadi pesan ramah
        log.warning("gambar gagal: %s", e)
        await status.edit_text("Gagal bikin gambar. Coba lagi nanti 🙏")
        return

    try:
        with open(tmp, "rb") as f:
            await update.message.reply_photo(photo=f, caption=prompt[:200])
        if not premium:
            inc_downloads_today(conn, user.id)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    try:
        await status.delete()
    except Exception:  # noqa: BLE001 - hapus status gagal ya udah
        pass


def register(app: Application, db):
    app.add_handler(CommandHandler("gambar", gambar_cmd))
    log.info("imagegen plugin registered")
