"""Plugin tools: olah foto & video langsung di Telegram.

Foto: foto -> PDF, convert format (PNG/JPG/WEBP), kompres foto,
hapus background (Replicate API / rembg lokal).
Video (butuh ffmpeg di server): kompres video, convert ke MP4,
potong 30 detik pertama.

Alur: /tools -> pilih tombol -> kirim foto/video -> hasil dikirim balik.
Jatah harian gabung dengan downloader (5/hari gratis, premium unlimited).
"""

import asyncio
import base64
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import inc_downloads_today, is_premium, upsert_user
from plugins.anonchat import is_in_session
from plugins.downloader import quota_ok

try:
    from PIL import Image
except ImportError:  # jangan matikan bot cuma gara2 dep belum diinstall
    Image = None

# rembg = dep BERAT (native: onnxruntime, cv2, numba). Di-import MALAS saat
# tool-nya beneran dipakai, dan error APAPUN waktu import tidak boleh
# mematikan bot — cuma tool hapus-background yang nonaktif.
_rembg_remove = None
_rembg_session = None
_rembg_failed = False


def _get_rembg():
    global _rembg_remove, _rembg_session, _rembg_failed
    if _rembg_remove is None and not _rembg_failed:
        try:
            from rembg import remove, new_session

            _rembg_remove = remove
            # Pin u2net (176MB): model default rembg versi baru (bria, 1GB+)
            # terlalu berat buat VPS kecil — pernah OOM.
            _rembg_session = new_session("u2net")
        except Exception as e:  # noqa: BLE001 - dep opsional, jangan matikan bot
            _rembg_failed = True
            log.warning("rembg tidak bisa dipakai: %r", e)
    return _rembg_remove


def _get_session():
    _get_rembg()
    return _rembg_session

log = logging.getLogger("serbabot.tools")

MAX_BYTES = 45 * 1024 * 1024
RMBG_TIMEOUT = 300  # rembg di CPU agak lama untuk foto besar

TOOL_LABEL = {
    "pdf": "🖼️ Foto → PDF",
    "to_png": "🔄 Convert ke PNG",
    "to_jpg": "🔄 Convert ke JPG",
    "to_webp": "🔄 Convert ke WEBP",
    "compress": "🗜️ Kompres foto",
    "rmbg": "🎨 Hapus background",
    "vcompress": "🗜️ Kompres video",
    "vconvert": "🎬 Video → MP4",
    "vtrim": "✂️ Potong 30 detik pertama",
}

VIDEO_ACTIONS = {"vcompress", "vconvert", "vtrim"}
FFMPEG_TIMEOUT = 180  # detik; video di CPU 2-core jangan kelamaan


# ---------- fungsi murni (gampang di-test) ----------

def img_to_pdf(src, dst):
    with Image.open(src) as im:
        im.convert("RGB").save(dst, "PDF")


def img_convert(src, dst, fmt):
    with Image.open(src) as im:
        if fmt in ("JPEG", "WEBP"):
            im.convert("RGB").save(dst, fmt, quality=92)
        else:
            im.save(dst, fmt)


def img_compress(src, dst, quality=60, max_side=1600):
    with Image.open(src) as im:
        im.thumbnail((max_side, max_side))
        im.convert("RGB").save(dst, "JPEG", quality=quality, optimize=True)


def img_rmbg(src, dst):
    remove = _get_rembg()
    if remove is None:
        raise RuntimeError("rembg belum bisa dipakai di server ini")
    with Image.open(src) as im:
        out = remove(im, session=_get_session())
        out.save(dst, "PNG")


# ---------- video via ffmpeg (fungsi murni, gampang di-test) ----------

def _run_ffmpeg(args, timeout=FFMPEG_TIMEOUT):
    """Jalankan ffmpeg. ffmpeg tidak ada -> RuntimeError('FFMPEG_OFF')."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    try:
        p = subprocess.run(cmd, timeout=timeout, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("FFMPEG_OFF")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg kelamaan (>3 menit), coba video yang lebih kecil")
    if p.returncode != 0:
        err = p.stderr.decode(errors="replace")[:200] if p.stderr else ""
        raise RuntimeError("ffmpeg gagal: %s" % err)


def vid_compress(src, dst):
    """Kompres video: lebar maks 1280px, crf 28, preset veryfast."""
    _run_ffmpeg([
        "-i", src,
        "-vf", "scale='min(1280,iw)':-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        dst,
    ])


def vid_to_mp4(src, dst):
    """Convert video apa pun ke MP4 (h264 + aac)."""
    _run_ffmpeg([
        "-i", src,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        dst,
    ])


def vid_trim(src, dst, seconds=30):
    """Potong N detik pertama. Tanpa re-encode -> cepat."""
    _run_ffmpeg(["-i", src, "-t", str(seconds), "-c", "copy", dst])


def _rmbg_job(src, dst):
    """Urutan: Replicate API (cepat, ~4 detik di GPU) -> rembg lokal
    (lambat tapi gratis, tanpa token)."""
    try:
        log.info("rmbg: coba via Replicate API...")
        _replicate_rmbg(src, dst)
        log.info("rmbg via Replicate API OK")
        return
    except Exception as e:  # noqa: BLE001 - API opsional, selalu ada fallback lokal
        if isinstance(e, RuntimeError) and str(e) == "NO_REPLICATE_TOKEN":
            log.info("REPLICATE_API_TOKEN belum di-set, pakai rembg lokal")
        else:
            log.warning("rmbg via API gagal (%r), fallback lokal", e)
    if _get_rembg() is None:
        raise RuntimeError("REM_BG_OFF")
    img_rmbg(src, dst)


# --- Hapus background via Replicate API -------------------------------------
# Model: cjwbw/rembg — ~$0.0037/foto, GPU L40S, kelar ~4 detik.
# Daftar gratis di replicate.com dapat $25 kredit (tanpa kartu kredit).
# Endpoint klasik /v1/predictions + version yang terverifikasi (2026-10-01).
REPLICATE_API = "https://api.replicate.com/v1/predictions"
REPLICATE_VERSION = "fb8af171cfa1616ddcf1242c093f9c46bcada5ad4cf6f2fbe8b81b330ec5c003"  # cjwbw/rembg
REPLICATE_TIMEOUT = 180


def _replicate_rmbg(src, dst, timeout=REPLICATE_TIMEOUT):
    token = os.environ.get("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("NO_REPLICATE_TOKEN")

    # Kecilkan dulu (maks 1600px, JPEG) biar upload + proses cepat.
    buf = io.BytesIO()
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((1600, 1600), Image.LANCZOS)
        im.save(buf, "JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    auth = {"Authorization": "Token " + token}

    def _create():
        req = urllib.request.Request(
            REPLICATE_API,
            data=json.dumps(
                {
                    "version": REPLICATE_VERSION,
                    "input": {"image": "data:image/jpeg;base64," + b64},
                }
            ).encode(),
            headers={**auth, "Content-Type": "application/json", "Prefer": "wait"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError("Replicate API %s: %s" % (e.code, e.read()[:200]))

    pred = _create()
    deadline = time.time() + timeout
    while pred.get("status") not in ("succeeded", "failed", "canceled"):
        if time.time() > deadline:
            raise RuntimeError("Replicate timeout")
        time.sleep(2)
        req = urllib.request.Request(
            "https://api.replicate.com/v1/predictions/" + pred["id"], headers=auth
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            pred = json.loads(r.read())

    if pred.get("status") != "succeeded":
        raise RuntimeError("Replicate gagal: %s" % str(pred.get("error"))[:200])
    out = pred.get("output")
    if isinstance(out, list):
        out = out[0] if out else None
    if not out:
        raise RuntimeError("Replicate: output kosong")

    req = urllib.request.Request(out, headers={"User-Agent": "SerbaBot/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r, open(dst, "wb") as f:
        f.write(r.read())


# ---------- handlers ----------

async def tools_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🖼️ Foto → PDF", callback_data="tool:pdf")],
        [InlineKeyboardButton("🔄 Convert format", callback_data="tool:convert")],
        [InlineKeyboardButton("🗜️ Kompres foto", callback_data="tool:compress")],
        [InlineKeyboardButton("🎨 Hapus background", callback_data="tool:rmbg")],
        [InlineKeyboardButton("🎬 Tools video", callback_data="tool:video")],
    ]
    await update.message.reply_text(
        "Pilih tools, terus kirim fotonya ya 📸",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def tool_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":", 1)[1]
    if action == "convert":
        kb = [
            [
                InlineKeyboardButton("PNG", callback_data="tool:to_png"),
                InlineKeyboardButton("JPG", callback_data="tool:to_jpg"),
                InlineKeyboardButton("WEBP", callback_data="tool:to_webp"),
            ],
            [InlineKeyboardButton("❌ Batal", callback_data="tool:cancel")],
        ]
        await q.edit_message_text(
            "Convert ke format apa?", reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    if action == "cancel":
        context.user_data.pop("pending_tool", None)
        await q.edit_message_text("Dibatalkan.")
        return
    if action == "video":
        kb = [
            [InlineKeyboardButton("🗜️ Kompres video", callback_data="tool:vcompress")],
            [InlineKeyboardButton("🎬 Video → MP4", callback_data="tool:vconvert")],
            [InlineKeyboardButton(
                "✂️ Potong 30 detik pertama", callback_data="tool:vtrim")],
            [InlineKeyboardButton("❌ Batal", callback_data="tool:cancel")],
        ]
        await q.edit_message_text(
            "Tools video — pilih:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    context.user_data["pending_tool"] = action
    if action in VIDEO_ACTIONS:
        await q.edit_message_text(
            f"{TOOL_LABEL[action]} — sekarang kirim videonya 🎬 (maks 45MB)\n"
            "/batal buat batalin."
        )
    else:
        await q.edit_message_text(
            f"{TOOL_LABEL[action]} — sekarang kirim fotonya 📸\n/batal buat batalin."
        )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_tool", None)
    await update.message.reply_text("Dibatalkan.")


async def photo_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("pending_tool")
    if not action:
        return
    user = update.effective_user
    if is_in_session(user.id):
        # lagi ngobrol anon: foto dianggap chat, bukan bahan tools
        return
    if action in VIDEO_ACTIONS:
        await update.message.reply_text(
            "Ini tools video — kirim videonya ya 🎬 (bukan foto)\n/batal buat batalin."
        )
        return
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_premium(conn, user.id)
    if not premium and not quota_ok(conn, user.id):
        await update.message.reply_text(
            "Jatah harian gratis lu habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return

    status = await update.message.reply_text("Lagi diproses... ⏳")
    tmpdir = tempfile.mkdtemp(prefix="serbatool_")
    try:
        src = os.path.join(tmpdir, "in")
        tgfile = await update.message.photo[-1].get_file()
        await tgfile.download_to_drive(src)

        if action == "pdf":
            dst = os.path.join(tmpdir, "hasil.pdf")
            await asyncio.to_thread(img_to_pdf, src, dst)
        elif action in ("to_png", "to_jpg", "to_webp"):
            fmt = {"to_png": "PNG", "to_jpg": "JPEG", "to_webp": "WEBP"}[action]
            ext = {"to_png": "png", "to_jpg": "jpg", "to_webp": "webp"}[action]
            dst = os.path.join(tmpdir, "hasil." + ext)
            await asyncio.to_thread(img_convert, src, dst, fmt)
        elif action == "compress":
            dst = os.path.join(tmpdir, "hasil-kompres.jpg")
            await asyncio.to_thread(img_compress, src, dst)
        elif action == "rmbg":
            dst = os.path.join(tmpdir, "hasil-nobg.png")
            # Semua di thread biar event loop tetap responsif (user bisa
            # /batal atau pakai fitur lain sambil nunggu). Pertama kali
            # pakai: model AI ~176MB diunduh dulu, agak lama.
            await asyncio.wait_for(
                asyncio.to_thread(_rmbg_job, src, dst), timeout=RMBG_TIMEOUT
            )
        else:
            raise RuntimeError("tool tidak dikenal: %s" % action)

        if os.path.getsize(dst) > MAX_BYTES:
            await status.edit_text("Hasilnya kegedean (>45MB), nggak bisa dikirim 🙏")
            return
        with open(dst, "rb") as f:
            await update.message.reply_document(document=f)
        if not premium:
            inc_downloads_today(conn, user.id)
        context.user_data.pop("pending_tool", None)
        await status.delete()
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("tools timeout (%s)", action)
        await status.edit_text("Kelamaan prosesnya. Coba foto yang lebih kecil 🙏")
    except RuntimeError as e:
        if str(e) == "REM_BG_OFF":
            await status.edit_text(
                "Hapus background belum aktif di server ini 🙏 Coba tools lain dulu."
            )
            context.user_data.pop("pending_tool", None)
        else:
            log.warning("tools gagal (%s): %s", action, e)
            await status.edit_text("Gagal proses fotonya. Coba foto lain 🙏")
    except Exception as e:  # noqa: BLE001 - foto aneh harus jadi pesan ramah
        log.warning("tools gagal (%s): %s", action, e)
        await status.edit_text("Gagal proses fotonya. Coba foto lain 🙏")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def video_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("pending_tool")
    if not action or action not in VIDEO_ACTIONS:
        return
    user = update.effective_user
    if is_in_session(user.id):
        # lagi ngobrol anon: video dianggap chat, bukan bahan tools
        return
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_premium(conn, user.id)
    if not premium and not quota_ok(conn, user.id):
        await update.message.reply_text(
            "Jatah harian gratis lu habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return

    status = await update.message.reply_text("Lagi diproses... ⏳")
    tmpdir = tempfile.mkdtemp(prefix="serbatool_")
    try:
        src = os.path.join(tmpdir, "in")
        tgfile = await update.message.video.get_file()
        await tgfile.download_to_drive(src)
        if os.path.getsize(src) > MAX_BYTES:
            await status.edit_text(
                "Videonya kegedean (>45MB), kirim yang lebih kecil 🙏"
            )
            return

        if action == "vcompress":
            dst = os.path.join(tmpdir, "hasil-kompres.mp4")
            await asyncio.to_thread(vid_compress, src, dst)
        elif action == "vconvert":
            dst = os.path.join(tmpdir, "hasil.mp4")
            await asyncio.to_thread(vid_to_mp4, src, dst)
        elif action == "vtrim":
            dst = os.path.join(tmpdir, "hasil-potong.mp4")
            await asyncio.to_thread(vid_trim, src, dst)
        else:
            raise RuntimeError("tool tidak dikenal: %s" % action)

        if os.path.getsize(dst) > MAX_BYTES:
            await status.edit_text("Hasilnya kegedean (>45MB), nggak bisa dikirim 🙏")
            return
        with open(dst, "rb") as f:
            await update.message.reply_video(video=f)
        if not premium:
            inc_downloads_today(conn, user.id)
        context.user_data.pop("pending_tool", None)
        await status.delete()
    except RuntimeError as e:
        if str(e) == "FFMPEG_OFF":
            await status.edit_text(
                "Tools video belum aktif di server ini 🙏 Coba tools foto dulu."
            )
            context.user_data.pop("pending_tool", None)
        else:
            log.warning("video tools gagal (%s): %s", action, e)
            await status.edit_text("Gagal proses videonya. Coba video lain 🙏")
    except Exception as e:  # noqa: BLE001 - video aneh harus jadi pesan ramah
        log.warning("video tools gagal (%s): %s", action, e)
        await status.edit_text("Gagal proses videonya. Coba video lain 🙏")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def register(app: Application, db):
    if Image is None:
        log.warning("pillow belum diinstall -> plugin tools nonaktif. pip install pillow")
        return
    app.add_handler(CommandHandler("tools", tools_cmd))
    app.add_handler(CommandHandler("batal", cancel_cmd))
    app.add_handler(CallbackQueryHandler(tool_choice, pattern=r"^tool:"))
    app.add_handler(MessageHandler(filters.PHOTO, photo_in))
    app.add_handler(MessageHandler(filters.VIDEO, video_in))
    log.info("tools plugin registered")
