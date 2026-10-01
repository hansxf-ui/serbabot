"""Plugin tools: olah foto langsung di Telegram.

- Foto -> PDF
- Convert format (PNG/JPG/WEBP)
- Kompres foto (resize + quality)
- Hapus background (butuh `pip install rembg`; model ~176MB diunduh
  otomatis saat pertama dipakai)

Alur: /tools -> pilih tombol -> kirim foto -> hasil dikirim balik.
Jatah harian gabung dengan downloader (5/hari gratis, premium unlimited).
"""

import asyncio
import logging
import os
import shutil
import tempfile

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

try:
    from rembg import remove as _rembg_remove
except ImportError:
    _rembg_remove = None

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
}


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
    if _rembg_remove is None:
        raise RuntimeError("rembg belum diinstall di server")
    with Image.open(src) as im:
        out = _rembg_remove(im)
        out.save(dst, "PNG")


# ---------- handlers ----------

async def tools_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🖼️ Foto → PDF", callback_data="tool:pdf")],
        [InlineKeyboardButton("🔄 Convert format", callback_data="tool:convert")],
        [InlineKeyboardButton("🗜️ Kompres foto", callback_data="tool:compress")],
        [InlineKeyboardButton("🎨 Hapus background", callback_data="tool:rmbg")],
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
    context.user_data["pending_tool"] = action
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
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_premium(conn, user.id)
    if not premium and not quota_ok(conn, user.id):
        await update.message.reply_text(
            "Jatah harian gratis lu habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return
    if action == "rmbg" and _rembg_remove is None:
        await update.message.reply_text(
            "Hapus background belum aktif di server ini 🙏 Coba tools lain dulu."
        )
        context.user_data.pop("pending_tool", None)
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
            await asyncio.wait_for(
                asyncio.to_thread(img_rmbg, src, dst), timeout=RMBG_TIMEOUT
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
    except Exception as e:  # noqa: BLE001 - foto aneh harus jadi pesan ramah
        log.warning("tools gagal (%s): %s", action, e)
        await status.edit_text("Gagal proses fotonya. Coba foto lain 🙏")
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
    log.info("tools plugin registered")
