"""Plugin Konten Premium: jual konten eksklusif via Telegram Stars (paid media).

/katalog — daftar konten + tombol beli pakai Stars. Setelah bayar,
PTB kirim update successful_payment -> pembelian dicatat, konten
dikirim ulang GRATIS. Item yang sudah dibeli bisa dikirim ulang
kapan aja tanpa bayar lagi.

Media di-generate via PIL sekali, lalu di-cache di /tmp.
Tanpa quota harian — dibatasi Stars.
"""
import logging
import os
import time
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputPaidMediaPhoto,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import upsert_user

log = logging.getLogger("serbabot.paidmedia")

CACHE_DIR = "/tmp/serbabot_paidmedia"

ITEMS = {
    "wallpaper": {
        "name": "Pack Wallpaper AI Sunset",
        "stars": 25,
        "desc": "3 wallpaper sunset gradien (1080×1920)",
    },
    "stiker": {
        "name": "Pack Stiker Premium",
        "stars": 40,
        "desc": "5 stiker emoji-style (512×512 PNG)",
    },
    "ebook": {
        "name": "E-book Mini: Trik Telegram",
        "stars": 15,
        "desc": "10 trik Telegram yang beneran berguna",
    },
}


def _font(size):
    return ImageFont.load_default(size=size)


def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


# ---------- generator konten (testable) ----------

SUNSETS = [
    ((255, 126, 95), (45, 20, 80)),     # oranye -> ungu tua
    ((255, 180, 100), (120, 30, 90)),   # emas -> magenta
    ((120, 180, 255), (20, 40, 110)),   # biru senja -> biru malam
]


def gen_wallpaper(i):
    """Satu wallpaper sunset gradien (1080x1920). i = 0..2."""
    top, bottom = SUNSETS[i % len(SUNSETS)]
    w, h = 1080, 1920
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    for y in range(h):
        t = y / (h - 1)
        px[0, y] = (
            round(top[0] + (bottom[0] - top[0]) * t),
            round(top[1] + (bottom[1] - top[1]) * t),
            round(top[2] + (bottom[2] - top[2]) * t),
        )
    img = strip.resize((w, h), Image.BILINEAR)
    d = ImageDraw.Draw(img)
    # matahari
    cy = int(h * 0.62)
    d.ellipse([w / 2 - 150, cy - 150, w / 2 + 150, cy + 150],
              fill=(255, 240, 200))
    # bukit siluet
    d.polygon([(0, h), (0, int(h * 0.78)), (w * 0.35, int(h * 0.70)),
               (w * 0.7, int(h * 0.80)), (w, int(h * 0.74)), (w, h)],
              fill=(20, 12, 40))
    out = BytesIO()
    img.save(out, "PNG")
    out.seek(0)
    return out


STICKERS = [
    ((255, 210, 63), "biasa", "SENYUM"),   # kuning, senyum standar
    ((255, 150, 150), "kedip", "KECE"),    # pink, kedip
    ((150, 230, 255), "kaget", "WOW"),     # biru, mulut O
    ((180, 255, 170), "dingin", "SANTUY"), # hijau, kacamata
    ((230, 170, 255), "love", "BUCIN"),    # ungu, mata hati
]


def gen_sticker(i):
    """Satu stiker emoji-style: lingkaran warna + wajah sederhana (512x512)."""
    color, face, word = STICKERS[i % len(STICKERS)]
    s = 512
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([16, 16, s - 16, s - 16], fill=color + (255,))
    cx, cy = s / 2, s / 2 - 20
    eye = (40, 52)
    if face == "kedip":
        d.ellipse([cx - 90 - eye[0] / 2, cy - eye[1] / 2,
                   cx - 90 + eye[0] / 2, cy + eye[1] / 2], fill="black")
        d.line([cx + 90 - 25, cy, cx + 90 + 25, cy], fill="black", width=10)
    elif face == "love":
        for ex in (cx - 90, cx + 90):
            d.polygon([(ex, cy + 28), (ex - 26, cy), (ex - 13, cy - 18),
                       (ex, cy - 6), (ex + 13, cy - 18), (ex + 26, cy)],
                      fill=(230, 40, 70))
    else:
        for ex in (cx - 90, cx + 90):
            d.ellipse([ex - eye[0] / 2, cy - eye[1] / 2,
                       ex + eye[0] / 2, cy + eye[1] / 2], fill="black")
    if face == "dingin":  # kacamata hitam
        d.rectangle([cx - 140, cy - 40, cx - 40, cy + 20], fill="black")
        d.rectangle([cx + 40, cy - 40, cx + 140, cy + 20], fill="black")
        d.line([cx - 40, cy - 20, cx + 40, cy - 20], fill="black", width=12)
    if face == "kaget":
        d.ellipse([cx - 35, cy + 90, cx + 35, cy + 160], fill="black")
    else:
        d.arc([cx - 80, cy + 60, cx + 80, cy + 180], start=20, end=160,
              fill="black", width=14)
    d.text((s / 2, s - 70), word, font=_font(56), fill=(40, 40, 40), anchor="mm")
    out = BytesIO()
    img.save(out, "PNG")
    out.seek(0)
    return out


EBOOK_TRIK = [
    ("Simpan pesan buat diri sendiri", "Chat ke 'Saved Messages' buat nyimpen link, foto, atau draf. Bisa diakses dari semua device."),
    ("Kirim tanpa suara", "Tahan tombol kirim -> 'Send Without Sound'. Chat tengah malam tanpa bangunin orang."),
    ("Jadwalkan pesan", "Tahan tombol kirim -> 'Schedule Message'. Cocok buat ngucapin ultah tepat jam 00:00."),
    ("Edit pesan yang udah kekirim", "Typo? Klik pesan -> Edit. Berlaku juga buat caption foto."),
    ("Folder chat", "Settings -> Chat Folders. Misahin grup kerja, keluarga, dan channel biar nggak campur aduk."),
    ("Mute + arsip biar anteng", "Arsipkan chat berisik + mute selamanya. Notif hilang, chat tetap bisa dibuka manual."),
    ("Cari di dalam chat", "Buka chat -> titik tiga -> Search. Bisa filter per tanggal buat nemu foto lama."),
    ("Kirim file gede", "Telegram bisa kirim file sampai 2GB per file (gratis). Buat backup video tanpa kompres pakai 'Send as File'."),
    ("Username biar nggak sebar nomor", "Settings -> Username. Orang bisa chat lu tanpa tahu nomor HP lu."),
    ("Kunci aplikasi", "Settings -> Privacy -> Passcode Lock. Chat aman walau HP dipinjam teman."),
]


def gen_ebook_bytes():
    lines = ["TRIK TELEGRAM — 10 trik yang beneran berguna", "=" * 45, ""]
    for n, (judul, isi) in enumerate(EBOOK_TRIK, 1):
        lines += [f"{n}. {judul}", f"   {isi}", ""]
    return ("\n".join(lines)).encode("utf-8")


# ---------- cache file ----------

def _media_files(item_id):
    """Return list path file media item ini (generate sekali, cache di /tmp)."""
    if item_id == "wallpaper":
        paths = []
        for i in range(3):
            p = _cache_path(f"wallpaper_{i}.png")
            if not os.path.exists(p):
                with open(p, "wb") as f:
                    f.write(gen_wallpaper(i).getvalue())
            paths.append(p)
        return paths
    if item_id == "stiker":
        paths = []
        for i in range(5):
            p = _cache_path(f"stiker_{i}.png")
            if not os.path.exists(p):
                with open(p, "wb") as f:
                    f.write(gen_sticker(i).getvalue())
            paths.append(p)
        return paths
    if item_id == "ebook":
        p = _cache_path("trik-telegram.txt")
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(gen_ebook_bytes())
        return [p]
    raise ValueError(f"item tidak dikenal: {item_id}")


# ---------- DB pembelian ----------

def _ensure_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS paidmedia_buys("
        "user_id INTEGER NOT NULL, item_id TEXT NOT NULL, "
        "bought_at TEXT NOT NULL, PRIMARY KEY(user_id, item_id))"
    )
    conn.commit()


def has_bought(conn, user_id, item_id):
    _ensure_table(conn)
    row = conn.execute(
        "SELECT 1 FROM paidmedia_buys WHERE user_id=? AND item_id=?",
        (user_id, item_id),
    ).fetchone()
    return bool(row)


def record_buy(conn, user_id, item_id):
    _ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO paidmedia_buys(user_id, item_id, bought_at)"
        " VALUES(?,?,?)",
        (user_id, item_id, time.strftime("%Y-%m-%dT%H:%M:%S")),
    )
    conn.commit()


# ---------- kirim konten ----------

async def _send_item(bot, chat_id, item_id):
    for p in _media_files(item_id):
        with open(p, "rb") as f:
            data = f.read()
        name = os.path.basename(p)
        if item_id == "ebook":
            await bot.send_document(chat_id, document=InputFile(BytesIO(data), filename=name))
        else:
            await bot.send_photo(chat_id, photo=InputFile(BytesIO(data), filename=name))


# ---------- handlers ----------

async def katalog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    lines = ["🌟 *KATALOG PREMIUM* — bayar pakai Telegram Stars:\n"]
    kb = []
    for iid, item in ITEMS.items():
        owned = has_bought(conn, user.id, iid)
        if owned:
            lines.append(f"✅ *{item['name']}* — dimiliki")
            kb.append([InlineKeyboardButton(
                "📩 Kirim ulang", callback_data=f"paidmedia:resend:{iid}")])
        else:
            lines.append(f"⭐{item['stars']} — *{item['name']}*\n   {item['desc']}")
            kb.append([InlineKeyboardButton(
                f"Beli ⭐{item['stars']}", callback_data=f"paidmedia:buy:{iid}")])
        lines.append("")
    await update.message.reply_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown",
    )


async def buy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    item_id = q.data.split(":")[2] if q.data.count(":") >= 2 else ""
    if item_id not in ITEMS:
        await q.message.reply_text("Item nggak dikenal.")
        return
    if has_bought(conn, user.id, item_id):
        await q.message.reply_text("Lu udah punya ini — dikirim ulang gratis ya 👇")
        await _send_item(context.bot, q.message.chat.id, item_id)
        return

    item = ITEMS[item_id]
    media = []
    for p in _media_files(item_id):
        with open(p, "rb") as f:
            media.append(InputPaidMediaPhoto(
                media=InputFile(BytesIO(f.read()), filename=os.path.basename(p))))
    await context.bot.send_paid_media(
        chat_id=q.message.chat.id,
        star_count=item["stars"],
        payload=f"paidmedia:{item_id}:{user.id}",
        media=media,
    )


async def resend_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    conn = context.bot_data["db"]

    item_id = q.data.split(":")[2] if q.data.count(":") >= 2 else ""
    if item_id not in ITEMS or not has_bought(conn, user.id, item_id):
        await q.message.reply_text("Lu belum beli item ini. Cek /katalog gih.")
        return
    await _send_item(context.bot, q.message.chat.id, item_id)


async def paid_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update successful_payment dari paid media: catat + kirim ulang gratis."""
    sp = update.message.successful_payment
    payload = (sp.invoice_payload or "")
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "paidmedia":
        return
    _, item_id, uid_raw = parts
    if item_id not in ITEMS:
        return
    try:
        buyer_id = int(uid_raw)
    except ValueError:
        return

    conn = context.bot_data["db"]
    record_buy(conn, buyer_id, item_id)
    chat_id = update.effective_chat.id
    await _send_item(context.bot, chat_id, item_id)
    await update.message.reply_text("Mantap! Kontennya dikirim ulang di atas 👆")


def register(app: Application, db):
    _ensure_table(db)
    app.add_handler(CommandHandler("katalog", katalog_cmd))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern=r"^paidmedia:buy:"))
    app.add_handler(CallbackQueryHandler(resend_cb, pattern=r"^paidmedia:resend:"))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid_ok))
    log.info("paidmedia plugin registered")
