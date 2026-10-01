"""Plugin Gacha Harian: koleksi karakter pahlawan Indonesia + meme lokal.

/gacha     — 1 pull GRATIS per hari (gambar kartu dikirim sebagai foto)
/gachaplus — pull ekstra, 50 koin per pull (tanpa batas harian selain koin)
/koleksi   — lihat daftar kartu milikmu + total unik

Duplikat otomatis diconvert jadi koin (common 5, rare 15, epic 40,
legendary 100). Sengaja tidak ada unlimited VIP: 1 gratis/hari + pull
berbayar koin itu desainnya.
"""
import io
import json
import logging
import os
import random

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import add_coins, get_coins, spend_coins, upsert_user

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # jangan matikan bot cuma gara2 dep belum diinstall
    Image = None

log = logging.getLogger("serbabot.gacha")

SCHEMA = """
CREATE TABLE IF NOT EXISTS gacha_pulls (
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS gacha_collection (
    user_id INTEGER NOT NULL,
    card_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, card_id)
);
"""

RARITY_BG = {
    "common": (150, 150, 150),
    "rare": (33, 150, 243),
    "epic": (156, 39, 176),
    "legendary": (255, 193, 7),
}
RARITY_EMOJI = {"common": "⚪", "rare": "🔵", "epic": "🟣", "legendary": "🟡"}
DUPE_COINS = {"common": 5, "rare": 15, "epic": 40, "legendary": 100}
PULL_PRICE = 50
CARD_W, CARD_H = 512, 768


def _load_cards():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cards.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


CARDS = _load_cards()
_CARDS_BY_RARITY = {}
for _c in CARDS:
    _CARDS_BY_RARITY.setdefault(_c["rarity"], []).append(_c)


def pick_rarity(roll=None):
    """Tentukan rarity. roll: callable -> float [0,1), atau float langsung.

    common 60%, rare 25%, epic 12%, legendary 3%.
    """
    r = roll() if callable(roll) else (random.random() if roll is None else roll)
    if r < 0.60:
        return "common"
    if r < 0.85:
        return "rare"
    if r < 0.97:
        return "epic"
    return "legendary"


def pick_card(rng=None):
    """Satu kartu acak sesuai probabilitas rarity. rng: modul/instance random."""
    rng = rng or random
    return rng.choice(_CARDS_BY_RARITY[pick_rarity(rng.random)])


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


def draw_card(card):
    """Gambar kartu 512x768 -> BytesIO PNG."""
    if Image is None:
        raise RuntimeError("pillow belum diinstall")
    rarity = card["rarity"]
    im = Image.new("RGB", (CARD_W, CARD_H), RARITY_BG[rarity])
    d = ImageDraw.Draw(im)
    ink = (255, 255, 255) if rarity != "legendary" else (50, 32, 0)
    stroke = (0, 0, 0) if rarity != "legendary" else (255, 255, 255)

    d.rectangle([8, 8, CARD_W - 8, CARD_H - 8], outline=ink, width=6)
    d.text((CARD_W // 2, 48), rarity.upper(), font=_font(44), fill=ink,
           stroke_width=2, stroke_fill=stroke, anchor="ma")

    y = 175
    name_font = _font(72)
    for line in _wrap(d, card["name"], name_font, CARD_W - 80):
        d.text((CARD_W // 2, y), line, font=name_font, fill=ink,
               stroke_width=3, stroke_fill=stroke, anchor="ma")
        y += 86
    d.text((CARD_W // 2, y + 12), card.get("title", ""), font=_font(34),
           fill=ink, stroke_width=2, stroke_fill=stroke, anchor="ma")

    desc_font = _font(30)
    lines = _wrap(d, card.get("desc", ""), desc_font, CARD_W - 80)
    y = CARD_H - 60 - len(lines) * 42
    for line in lines:
        d.text((CARD_W // 2, y), line, font=desc_font, fill=ink,
               stroke_width=2, stroke_fill=stroke, anchor="ma")
        y += 42

    buf = io.BytesIO()
    im.save(buf, "PNG")
    buf.seek(0)
    return buf


def _today():
    import datetime

    return datetime.date.today().isoformat()


def _give_card(conn, user_id, card):
    """Masukkan kartu ke koleksi. Return bonus koin kalau duplikat, else 0."""
    conn.executescript(SCHEMA)
    row = conn.execute(
        "SELECT count FROM gacha_collection WHERE user_id=? AND card_id=?",
        (user_id, card["id"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO gacha_collection(user_id, card_id, count) VALUES(?,?,1) "
        "ON CONFLICT(user_id, card_id) DO UPDATE SET count=count+1",
        (user_id, card["id"]),
    )
    conn.commit()
    if row:
        bonus = DUPE_COINS[card["rarity"]]
        add_coins(conn, user_id, bonus, "gacha duplikat %s" % card["name"])
        return bonus
    return 0


def _card_caption(card, bonus):
    rarity = card["rarity"]
    cap = (
        "🎴 %s\n%s %s — %s\n\n%s"
        % (card["name"], RARITY_EMOJI[rarity], rarity.upper(),
           card.get("title", ""), card.get("desc", ""))
    )
    if bonus:
        cap += "\n\n♻️ Duplikat! Diconvert jadi +%d koin 🪙" % bonus
    return cap


async def _send_pull(update, conn, user):
    card = pick_card()
    bonus = _give_card(conn, user.id, card)
    img = draw_card(card)
    await update.message.reply_photo(photo=img, caption=_card_caption(card, bonus))


async def gacha_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    conn.executescript(SCHEMA)

    today = _today()
    row = conn.execute(
        "SELECT count FROM gacha_pulls WHERE user_id=? AND date=?",
        (user.id, today),
    ).fetchone()
    if row and row[0] > 0:
        await update.message.reply_text(
            "Pull gratis hari ini udah kepake 😅\n"
            "Mau lagi? /gachaplus (50 koin per pull)"
        )
        return
    conn.execute(
        "INSERT INTO gacha_pulls(user_id, date, count) VALUES(?,?,1) "
        "ON CONFLICT(user_id, date) DO UPDATE SET count=count+1",
        (user.id, today),
    )
    conn.commit()
    await _send_pull(update, conn, user)


async def gachaplus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    conn.executescript(SCHEMA)

    if not spend_coins(conn, user.id, PULL_PRICE, "gacha pull ekstra"):
        await update.message.reply_text(
            "Koin lu kurang (butuh %d koin). /checkin dulu buat kumpulin! 🪙"
            % PULL_PRICE
        )
        return
    await _send_pull(update, conn, user)


async def koleksi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    conn.executescript(SCHEMA)

    rows = conn.execute(
        "SELECT card_id, count FROM gacha_collection WHERE user_id=?",
        (user.id,),
    ).fetchall()
    if not rows:
        await update.message.reply_text(
            "Koleksi lu masih kosong 😅 /gacha dulu buat pull gratis hari ini!"
        )
        return
    by_id = {c["id"]: c for c in CARDS}
    lines = ["📚 Koleksi Gacha Lu (%d kartu unik)" % len(rows)]
    for rarity in ("legendary", "epic", "rare", "common"):
        owned = [(by_id[r[0]], r[1]) for r in rows
                 if r[0] in by_id and by_id[r[0]]["rarity"] == rarity]
        if not owned:
            continue
        lines.append("\n%s %s" % (RARITY_EMOJI[rarity], rarity.upper()))
        for card, n in sorted(owned, key=lambda x: x[0]["name"]):
            lines.append("  • %s%s" % (card["name"], " x%d" % n if n > 1 else ""))
    await update.message.reply_text("\n".join(lines))


def register(app: Application, db):
    if Image is None:
        log.warning("pillow belum diinstall -> plugin gacha nonaktif")
        return
    db.executescript(SCHEMA)
    db.commit()
    app.add_handler(CommandHandler("gacha", gacha_cmd))
    app.add_handler(CommandHandler("gachaplus", gachaplus_cmd))
    app.add_handler(CommandHandler("koleksi", koleksi_cmd))
    log.info("gacha plugin registered")
