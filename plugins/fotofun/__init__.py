"""Plugin Foto Fun: tempel foto user ke template meme via PIL.

/memein — WAJIB reply ke foto. Pilih template via inline keyboard,
bayar 20 koin per generate (VIP pun bayar — ini koin sink, bukan quota).
Tanpa jatah harian, dibatasi koin.

BATASAN JUJUR: foto ditempel di posisi fix per template, TANPA face
detection. Ini disengaja & aman dari deepfake.
"""
import logging
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from db import get_coins, spend_coins, upsert_user

log = logging.getLogger("serbabot.fotofun")

HARGA = 20
CANVAS = (800, 1000)

# posisi fix foto per template: (x, y, w, h)
BOX = {
    "wanted": (120, 250, 560, 420),
    "breaking": (120, 200, 560, 420),
    "piala": (120, 280, 560, 420),
}

TEMPLATE_LABEL = {
    "wanted": "🤠 Wanted",
    "breaking": "📺 Breaking News",
    "piala": "🏆 Piala Juara",
}

_template_cache = {}


def _font(size):
    return ImageFont.load_default(size=size)


def _cover_crop(img, bw, bh):
    """Resize cover (jaga rasio, TIDAK gepeng): scale = max(bw/iw, bh/ih),
    lalu crop tengah ke box."""
    iw, ih = img.size
    scale = max(bw / iw, bh / ih)
    nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    x, y = (nw - bw) // 2, (nh - bh) // 2
    return img.crop((x, y, x + bw, y + bh))


def _draw_wanted():
    w, h = CANVAS
    base = Image.new("RGB", (w, h), (138, 90, 51))
    d = ImageDraw.Draw(base)
    d.rectangle([20, 20, w - 20, h - 20], outline=(60, 38, 18), width=6)
    d.rectangle([36, 36, w - 36, h - 36], outline=(210, 170, 110), width=3)
    f_big, f_mid = _font(110), _font(44)
    d.text((w / 2, 130), "WANTED", font=f_big, fill=(48, 28, 12), anchor="mm")
    d.text((w / 2, 215), "DEAD OR ALIVE", font=f_mid, fill=(60, 38, 18), anchor="mm")
    d.text((w / 2, 800), "REWARD $1.000.000", font=f_mid, fill=(48, 28, 12), anchor="mm")
    d.text((w / 2, 880), "hubungi sheriff terdekat", font=_font(32),
           fill=(80, 52, 26), anchor="mm")
    return base


def _draw_breaking():
    w, h = CANVAS
    base = Image.new("RGB", (w, h), (240, 240, 240))
    d = ImageDraw.Draw(base)
    d.rectangle([0, 0, w, 150], fill=(200, 20, 20))
    d.text((w / 2, 78), "BREAKING NEWS", font=_font(72), fill="white", anchor="mm")
    d.rectangle([0, h - 160, w, h], fill=(200, 20, 20))
    d.text((w / 2, h - 80), "LAPORAN LANGSUNG DARI LAPANGAN",
           font=_font(36), fill="white", anchor="mm")
    d.rectangle([100, 180, 700, 640], outline=(200, 20, 20), width=4)
    return base


def _draw_piala():
    w, h = CANVAS
    base = Image.new("RGB", (w, h), (176, 128, 24))
    d = ImageDraw.Draw(base)
    d.rectangle([20, 20, w - 20, h - 20], outline=(255, 215, 90), width=8)
    d.text((w / 2, 130), "★ JUARA ★", font=_font(88), fill=(255, 240, 180), anchor="mm")
    d.text((w / 2, 215), "SELAMAT KEPADA SANG PEMENANG", font=_font(32),
           fill=(255, 235, 170), anchor="mm")
    # taburan bintang kecil, pola fix (deterministik)
    for i in range(24):
        x = 60 + (i * 137) % (w - 120)
        y = 760 + (i * 89) % 160
        d.text((x, y), "★", font=_font(28), fill=(255, 225, 120))
    # bingkai emas tebal di sekeliling box foto
    x, y, bw, bh = BOX["piala"]
    d.rectangle([x - 14, y - 14, x + bw + 14, y + bh + 14],
                outline=(255, 215, 90), width=10)
    return base


_DRAWERS = {"wanted": _draw_wanted, "breaking": _draw_breaking, "piala": _draw_piala}


def apply_template(photo_bytes, template_id):
    """Tempel foto ke template. Return BytesIO PNG.

    Raise ValueError kalau template_id tidak dikenal.
    """
    if template_id not in _DRAWERS:
        raise ValueError(f"template tidak dikenal: {template_id}")
    img = Image.open(BytesIO(photo_bytes)).convert("RGB")
    base = _template_cache.get(template_id)
    if base is None:
        base = _DRAWERS[template_id]()
        _template_cache[template_id] = base
    base = base.copy()
    x, y, bw, bh = BOX[template_id]
    base.paste(_cover_crop(img, bw, bh), (x, y))
    out = BytesIO()
    base.save(out, "PNG")
    out.seek(0)
    return out


async def memein_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    reply = update.message.reply_to_message
    if not reply or not reply.photo:
        await update.message.reply_text(
            "Reply ke foto dulu gih 📸\nContoh: reply foto + ketik /memein"
        )
        return
    file_id = reply.photo[-1].file_id
    context.bot_data[f"memein:{user.id}"] = file_id
    kb = [[InlineKeyboardButton(TEMPLATE_LABEL[t], callback_data=f"memein:{t}")]
           for t in ("wanted", "breaking", "piala")]
    await update.message.reply_text(
        f"Pilih template-nya! ({HARGA} koin per generate 🪙)",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def memein_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    tpl = q.data.split(":", 1)[1] if ":" in q.data else ""
    if tpl not in _DRAWERS:
        await q.message.reply_text("Template nggak dikenal. /memein lagi gih.")
        return

    file_id = context.bot_data.get(f"memein:{user.id}")
    if not file_id:
        await q.message.reply_text(
            "Fotonya udah kadaluarsa, reply foto + /memein lagi gih 📸"
        )
        return

    tgfile = await context.bot.get_file(file_id)
    photo_bytes = bytes(await tgfile.download_as_bytearray())

    if not spend_coins(conn, user.id, HARGA, f"memein {tpl}"):
        await q.message.reply_text(
            f"koin kurang ({HARGA}), /checkin dulu gih 🪙\n"
            f"Saldo: {get_coins(conn, user.id)}"
        )
        return

    out = apply_template(photo_bytes, tpl)
    await q.message.reply_photo(
        photo=out, caption=f"{TEMPLATE_LABEL[tpl]} — {HARGA} koin 🪙"
    )


def register(app: Application, db):
    app.add_handler(CommandHandler("memein", memein_cmd))
    app.add_handler(CallbackQueryHandler(memein_pick, pattern=r"^memein:"))
    log.info("fotofun plugin registered")
