"""Plugin downloader: download video dari link via yt-dlp.

Dukung: TikTok, Instagram Reels, YouTube, X, Facebook (yt-dlp yang urus).

Freemium: user gratis = 5 download/hari, premium = unlimited.
Tidak mengganggu sesi anonchat: URL yang dikirim saat sesi aktif tetap
di-relay sebagai chat biasa, bukan di-download.
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import (
    get_downloads_today,
    inc_downloads_today,
    is_premium,
    upsert_user,
)
from plugins.anonchat import is_in_session

try:
    import yt_dlp
except ImportError:  # jangan matikan bot cuma gara2 dep belum diinstall
    yt_dlp = None

log = logging.getLogger("serbabot.downloader")

DAILY_FREE_LIMIT = 5
MAX_BYTES = 45 * 1024 * 1024  # limit file bot Telegram 50MB, kita 45MB biar aman

URL_RE = re.compile(r"https?://\S+")


def quota_ok(conn, user_id):
    """True kalau user boleh download: premium, atau jatah harian masih ada."""
    if is_premium(conn, user_id):
        return True
    return get_downloads_today(conn, user_id) < DAILY_FREE_LIMIT


def _fetch(url):
    """Blocking: download via yt-dlp, return path file. Dipanggil via to_thread."""
    tmpdir = tempfile.mkdtemp(prefix="serbadl_")
    base_opts = {
        "format": "best[filesize<45M]/best",
        "outtmpl": os.path.join(tmpdir, "%(title).50s-%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    # Cookies dari sesi login asli (format Netscape) bikin YouTube nganggep
    # request dari server sebagai user beneran, bukan robot. Taruh di
    # /opt/serbabot/cookies.txt atau set env SERBABOT_COOKIES ke path lain.
    cookie_file = os.environ.get("SERBABOT_COOKIES", "/opt/serbabot/cookies.txt")
    if os.path.exists(cookie_file):
        base_opts["cookiefile"] = cookie_file

    def _run(extra_args):
        opts = dict(base_opts)
        if extra_args:
            opts["extractor_args"] = extra_args
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    try:
        # Client default dulu: daftar format paling lengkap.
        path = _run(None)
    except Exception as e:
        # YouTube kadang nantang "login dulu" ke IP datacenter (VPS).
        # Kalau itu masalahnya, coba lagi pakai player client android
        # yang biasanya lolos dari tantangan itu.
        if "Sign in to confirm" not in str(e):
            raise
        log.warning("youtube bot-check, coba player_client android: %s", e)
        path = _run({"youtube": {"player_client": ["android"]}})
    if not os.path.exists(path):
        # kadang hasil merge namanya beda -> ambil file terbesar di tmpdir
        files = [
            os.path.join(tmpdir, f)
            for f in os.listdir(tmpdir)
            if os.path.isfile(os.path.join(tmpdir, f))
        ]
        if not files:
            raise RuntimeError("file hasil download tidak ketemu")
        path = max(files, key=os.path.getsize)
    return path


def _is_tiktok(url):
    return "tiktok.com" in url.lower()


def _fetch_tiktok_api(url):
    """Fallback tanpa login untuk TikTok via API publik tikwm (urllib stdlib,
    tanpa API key). Dipakai kalau yt-dlp diblokir IP server. Return path file."""
    api = "https://www.tikwm.com/api/?url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        import json
        data = json.load(r)
    if data.get("code") != 0:
        raise RuntimeError("tikwm: %s" % data.get("msg", "unknown"))
    d = data.get("data") or {}
    video_url = d.get("play") or d.get("wmplay") or d.get("hdplay")
    if not video_url:
        raise RuntimeError("tikwm: tidak ada URL video di respons")
    tmpdir = tempfile.mkdtemp(prefix="serbadl_")
    path = os.path.join(tmpdir, "tiktok-%s.mp4" % d.get("id", "video"))
    req = urllib.request.Request(
        video_url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tikwm.com/"},
    )
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)
    return path


async def _download_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, url):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_premium(conn, user.id)

    if not premium and not quota_ok(conn, user.id):
        await update.message.reply_text(
            "Jatah download gratis lu hari ini habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return

    status = await update.message.reply_text("Lagi download... ⏳")
    try:
        path = await asyncio.to_thread(_fetch, url)
    except Exception as e:  # noqa: BLE001 - link mati/unsupported harus jadi pesan ramah
        log.warning("download gagal (%s): %s", url, e)
        if _is_tiktok(url):
            # TikTok memblokir IP datacenter -> coba jalur API tanpa login
            await status.edit_text("Coba jalur lain... ⏳")
            try:
                path = await asyncio.to_thread(_fetch_tiktok_api, url)
            except Exception as e2:
                log.warning("fallback tikwm gagal (%s): %s", url, e2)
                await status.edit_text(
                    "Gagal download. Link-nya salah/kedaluwarsa, atau TikTok lagi ketat."
                )
                return
        else:
            await status.edit_text(
                "Gagal download. Link-nya salah/kedaluwarsa, atau situsnya nggak didukung."
            )
            return

    try:
        if os.path.getsize(path) > MAX_BYTES:
            await status.edit_text(
                "Filenya kegedean (>45MB), nggak bisa dikirim lewat bot. Coba link lain."
            )
            return
        with open(path, "rb") as f:
            if path.lower().endswith(".mp4"):
                await update.message.reply_video(video=f)
            else:
                await update.message.reply_document(document=f)
        if not premium:
            inc_downloads_today(conn, user.id)
        await status.delete()
    finally:
        try:
            os.remove(path)
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass


async def dl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Pakai: /dl <link video>\n"
            "Contoh: /dl https://www.tiktok.com/@user/video/123\n\n"
            "Atau langsung kirim link-nya aja ke sini."
        )
        return
    await _download_flow(update, context, context.args[0].rstrip(".,!?;)"))


async def url_watcher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jalan duluan (group=-1) sebelum relay anonchat. Kalau user lagi dalam
    sesi anon, URL dibiarkan lewat (di-relay sebagai chat). Kalau bukan URL
    atau lagi sesi, return biasa. Kalau diurus di sini, stop propagasi biar
    anonchat nggak ikut bales 'belum nyambung'."""
    text = update.message.text or ""
    m = URL_RE.search(text)
    if not m:
        return
    if is_in_session(update.effective_user.id):
        return
    await _download_flow(update, context, m.group(0).rstrip(".,!?;)"))
    raise ApplicationHandlerStop


def register(app: Application, db):
    if yt_dlp is None:
        log.warning("yt-dlp belum diinstall -> plugin downloader nonaktif. pip install yt-dlp")
        return
    app.add_handler(CommandHandler("dl", dl_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, url_watcher), group=-1)
    log.info("downloader plugin registered")
