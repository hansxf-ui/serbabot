"""Plugin meme & stiker.

/meme <atas>|<bawah> — reply ke foto ATAU kirim foto dengan caption:
/meme — jadikan meme (teks Impact-style: putih, outline hitam, uppercase).
    Non-VIP: watermark kecil "SerbaBot" di pojok kanan bawah.
/stiker               — reply ke foto ATAU kirim foto dengan caption /stiker:
    jadikan stiker Telegram (resize sisi terpanjang 512px, PNG,
    masuk sticker set per user).

Jatah: /meme 10/hari, /stiker 5/hari. VIP unlimited.
"""
import io
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputSticker, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import get_quota_today, inc_quota_today, is_vip, upsert_user

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # jangan matikan bot cuma gara2 dep belum diinstall
    Image = None

log = logging.getLogger("serbabot.meme")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meme_sets (
    user_id INTEGER PRIMARY KEY,
    set_name TEXT NOT NULL
);
"""

MEME_LIMIT = 10
STIKER_LIMIT = 5
STIKER_EMOJI = "🙂"


def _font(size):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:  # noqa: BLE001 - fallback font default
        pass
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _draw_meme_text(draw, text, w, h, pos):
    """Teks atas/bawah ala meme: putih, outline hitam, uppercase."""
    text = (text or "").strip().upper()
    if not text:
        return
    size = w // 8
    lines, font = [], _font(size)
    while size >= 14:
        font = _font(size)
        lines = _wrap(draw, text, font, w - 40)
        if all(draw.textlength(l, font=font) <= w - 40 for l in lines):
            break
        size -= 4
    stroke = max(2, size // 18)
    lh = int(size * 1.15)
    total = lh * len(lines)
    y = 12 if pos == "top" else h - total - 12
    for line in lines:
        draw.text((w // 2, y), line, font=font, fill="white",
                  stroke_width=stroke, stroke_fill="black", anchor="ma")
        y += lh


def make_meme(photo_bytes, top, bottom, watermark=True):
    """Bikin meme dari foto -> BytesIO PNG."""
    if Image is None:
        raise RuntimeError("pillow belum diinstall")
    im = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    draw = ImageDraw.Draw(im)
    w, h = im.size
    _draw_meme_text(draw, top, w, h, "top")
    _draw_meme_text(draw, bottom, w, h, "bottom")
    if watermark:
        f = _font(max(14, w // 45))
        txt = "SerbaBot"
        tw = draw.textlength(txt, font=f)
        draw.text((w - tw - 10, h - 28), txt, font=f, fill="white",
                  stroke_width=1, stroke_fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    buf.seek(0)
    return buf


def photo_to_sticker(photo_bytes):
    """Foto -> BytesIO PNG, sisi terpanjang tepat 512px (jaga rasio)."""
    if Image is None:
        raise RuntimeError("pillow belum diinstall")
    im = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    w, h = im.size
    m = max(w, h)
    if m != 512:
        s = 512 / m
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    buf.seek(0)
    return buf


async def _photo_bytes(update):
    """Ambil foto dari pesan yang di-reply ATAU dari foto yang dikirim
    bareng caption perintah (dua-duanya didukung)."""
    msg = update.message
    rmsg = msg.reply_to_message
    target = None
    if rmsg and rmsg.photo:
        target = rmsg
    elif msg.photo:
        target = msg
    if target is None:
        return None
    tgfile = await target.photo[-1].get_file()
    return bytes(await tgfile.download_as_bytearray())


async def _caption_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router untuk perintah yang dikirim sebagai caption foto.

    CommandHandler PTB sengaja tidak menangani caption, jadi foto +
    caption /stiker atau /meme ditangani di sini (sesuai anjuran PTB:
    MessageHandler + filters.CAPTION).
    """
    msg = update.message
    caption = (msg.caption or "").strip()
    if not caption.startswith("/"):
        return
    parts = caption.split(None, 1)
    cmd = parts[0].split("@")[0].lower()  # buang @namabot kalau ada
    rest = parts[1] if len(parts) > 1 else ""
    if cmd == "/stiker":
        context.args = []
        await stiker_cmd(update, context)
    elif cmd == "/meme":
        context.args = rest.split()
        await meme_cmd(update, context)


async def meme_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    data = await _photo_bytes(update)
    if data is None:
        await update.message.reply_text(
            "Cara pakai (2 cara):\n"
            "1. Reply ke sebuah foto, terus kirim:\n"
            "/meme TEKS ATAS|TEKS BAWAH\n"
            "2. Kirim foto dengan caption:\n"
            "/meme TEKS ATAS|TEKS BAWAH\n\n"
            "Contoh: /meme KALAU DITANYA KAPAN NIKAH|GUE: ..."
        )
        return

    vip = is_vip(conn, user.id, context.bot_data.get("admin_id"))
    if not vip and get_quota_today(conn, "meme", user.id) >= MEME_LIMIT:
        await update.message.reply_text(
            "Jatah /meme hari ini habis (10/hari). Besok lagi ya, "
            "atau upgrade biar unlimited: /premium"
        )
        return

    raw = " ".join(context.args or [])
    top, _, bottom = raw.partition("|")
    try:
        img = make_meme(data, top.strip(), bottom.strip(), watermark=not vip)
    except Exception as e:  # noqa: BLE001 - foto aneh harus jadi pesan ramah
        log.warning("meme gagal: %s", e)
        await update.message.reply_text("Gagal bikin memenya. Coba foto lain 🙏")
        return
    await update.message.reply_photo(photo=img)
    if not vip:
        inc_quota_today(conn, "meme", user.id)


async def stiker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    conn.executescript(SCHEMA)

    data = await _photo_bytes(update)
    if data is None:
        await update.message.reply_text(
            "Cara pakai (2 cara):\n"
            "1. Reply ke sebuah foto, terus kirim: /stiker\n"
            "2. Kirim foto dengan caption: /stiker"
        )
        return

    vip = is_vip(conn, user.id, context.bot_data.get("admin_id"))
    if not vip and get_quota_today(conn, "stiker", user.id) >= STIKER_LIMIT:
        await update.message.reply_text(
            "Jatah /stiker hari ini habis (5/hari). Besok lagi ya, "
            "atau upgrade biar unlimited: /premium"
        )
        return

    try:
        sticker_png = photo_to_sticker(data)
    except Exception as e:  # noqa: BLE001 - foto aneh harus jadi pesan ramah
        log.warning("stiker gagal: %s", e)
        await update.message.reply_text("Gagal bikin stikernya. Coba foto lain 🙏")
        return

    me = await context.bot.get_me()
    set_name = "serba_%d_by_%s" % (user.id, me.username)
    sticker = InputSticker(
        sticker=sticker_png, emoji_list=[STIKER_EMOJI], format="static"
    )
    row = conn.execute(
        "SELECT set_name FROM meme_sets WHERE user_id=?", (user.id,)
    ).fetchone()
    try:
        if row:
            await context.bot.add_sticker_to_set(user.id, row[0], sticker)
        else:
            title = "Stiker %s" % (user.first_name or "Serba")
            await context.bot.create_new_sticker_set(
                user.id, set_name, title, stickers=[sticker]
            )
            conn.execute(
                "INSERT INTO meme_sets(user_id, set_name) VALUES(?,?)",
                (user.id, set_name),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001 - nama set dipakai dsb: jujur aja
        log.warning("stiker set gagal: %s", e)
        await update.message.reply_text(
            "Gagal masukin ke sticker set: %s 🙏" % e
        )
        return

    # Kirim balik stikernya ke chat: user langsung lihat hasilnya detik itu
    # juga (real-time) dan bisa mengetuknya untuk buka pack-nya. Tanpa ini,
    # user dipaksa buka-tutup aplikasi karena preview link t.me di-cache
    # server Telegram dan tidak refresh saat stiker ditambahkan.
    # Tombol "Tambah ke Stikerku" selalu disertakan biar aksi nambahin
    # pack-nya jelas dan satu ketuk, tidak tergantung mengetuk stiker.
    add_url = "https://t.me/addstickers/%s" % set_name
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("➕ Tambah ke Stikerku", url=add_url)]]
    )
    sticker_png.seek(0)
    await update.message.reply_sticker(sticker=sticker_png)

    if row:
        await update.message.reply_text(
            "Nambah 1 stiker ke pack lu ✓",
            reply_markup=kb,
        )
    else:
        # Pack baru: user wajib tap tombol sekali biar pack-nya masuk panel.
        await update.message.reply_text(
            "Pack stiker lu jadi! 🎉\n"
            "Ketuk tombol di bawah SEKALI biar pack-nya masuk ke menu stiker:",
            reply_markup=kb,
        )
    if not vip:
        inc_quota_today(conn, "stiker", user.id)


def register(app: Application, db):
    if Image is None:
        log.warning("pillow belum diinstall -> plugin meme nonaktif")
        return
    db.executescript(SCHEMA)
    db.commit()
    app.add_handler(CommandHandler("meme", meme_cmd))
    app.add_handler(CommandHandler("stiker", stiker_cmd))
    # CommandHandler tidak menangani caption (kebijakan PTB) -> router sendiri
    app.add_handler(MessageHandler(
        filters.PHOTO & filters.CaptionRegex(r"^/(stiker|meme)(@\w+)?(\s|$)"),
        _caption_router,
    ))
    log.info("meme plugin registered")
