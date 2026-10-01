"""Plugin /premium — upgrade premium via Telegram Stars.

Digital goods cuma boleh dibayar lewat Stars (kebijakan Telegram), jadi
jalur pembayaran manual lama dihapus total. Tier:

- 50 ⭐  (~Rp12rb) → 30 hari premium
- 100 ⭐ (~Rp23rb) → 30 hari premium

Beli ulang sebelum habis = jatah ditumpuk (grant_premium_days).
"""

import datetime
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from db import grant_premium_days, is_premium, premium_until, upsert_user

log = logging.getLogger("serbabot.premium")

TIERS = {50: "~Rp12rb", 100: "~Rp23rb"}
DAYS = 30
INVOICE_TITLE = "SerbaBot Premium 30 hari"

PAYMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS premium_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    stars INTEGER NOT NULL,
    telegram_payment_charge_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

_BULAN = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
          "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _fmt_tanggal(iso_ts):
    """'2026-11-05T20:00:00' -> '5 Nov 2026'. Gagal parse = kembalikan asli."""
    try:
        dt = datetime.datetime.fromisoformat(iso_ts)
        return f"{dt.day} {_BULAN[dt.month]} {dt.year}"
    except (ValueError, TypeError, IndexError):
        return iso_ts


def _db(context):
    return context.bot_data["db"]


def _kb_beli():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{n} ⭐ — {TIERS[n]} / 30 hari",
                             callback_data=f"premium:buy:{n}")
        for n in sorted(TIERS)
    ]])


def _teks_status(conn, user_id):
    if is_premium(conn, user_id):
        sampai = _fmt_tanggal(premium_until(conn, user_id))
        return (f"⭐ Premium aktif sampai *{sampai}*\n"
                "Mau perpanjang? Beli lagi di bawah, "
                "jatahnya langsung numpuk.\n")
    return "Lu belum premium nih.\n"


async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(_db(context), user.id, user.username)
    teks = (
        "⭐ *SerbaBot Premium*\n\n"
        + _teks_status(_db(context), user.id)
        + "\nGratis vs Premium:\n"
        "• Jatah harian: 5 → ♾️ unlimited\n"
        "• Watermark di meme/foto: ada → hilang\n"
        "• Antrean prioritas pas server rame\n\n"
        "Bayar pakai Telegram Stars — langsung dari chat ini, "
        "nggak perlu transfer-transfer:"
    )
    await update.message.reply_text(
        teks, reply_markup=_kb_beli(), parse_mode="Markdown"
    )


async def buy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        stars = int(q.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await q.message.reply_text("Pilihan paketnya nggak dikenal 😅 /premium lagi gih.")
        return
    if stars not in TIERS:
        await q.message.reply_text("Pilihan paketnya nggak dikenal 😅 /premium lagi gih.")
        return
    try:
        await context.bot.send_invoice(
            chat_id=q.message.chat_id,
            title=INVOICE_TITLE,
            description=f"SerbaBot Premium {DAYS} hari ({stars} Stars).",
            payload=f"premium:{stars}:{q.from_user.id}",
            provider_token="",  # WAJIB kosong untuk Telegram Stars
            currency="XTR",
            prices=[LabeledPrice(f"Premium {DAYS} hari", stars)],
        )
    except Exception as e:  # noqa: BLE001 - user harus tahu kenapa gagal
        log.warning("send_invoice gagal: %s", e)
        await q.message.reply_text(
            "Duh, gagal buka halaman bayar Stars 😅 "
            "Coba lagi sebentar lagi ya."
        )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jawab pre-checkout ≤10 detik. Payload harus 'premium:<n>:<user_id' dan
    user_id-nya sama dengan yang bayar."""
    q = update.pre_checkout_query
    payload = q.invoice_payload or ""
    parts = payload.split(":")
    ok = (
        len(parts) == 3
        and parts[0] == "premium"
        and parts[1] in ("50", "100")
        and parts[2] == str(q.from_user.id)
    )
    if ok:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Pesanan nggak valid. /premium lagi gih.")
    return ok


async def paid_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pay = update.message.successful_payment
    # Abaikan payload milik plugin lain (mis. jaseb)
    if not (pay.invoice_payload or "").startswith("premium:"):
        return
    conn = _db(context)
    user = update.effective_user
    _, stars, uid = pay.invoice_payload.split(":", 2)
    user_id = int(uid)
    upsert_user(conn, user_id, user.username if user else None)
    sampai = grant_premium_days(conn, user_id, DAYS)
    conn.execute(
        "INSERT INTO premium_payments(user_id, stars, telegram_payment_charge_id,"
        " created_at) VALUES(?,?,?,datetime('now'))",
        (user_id, int(stars), pay.telegram_payment_charge_id),
    )
    conn.commit()
    log.info("premium: user %s bayar %s stars (%s)", user_id, stars,
             pay.telegram_payment_charge_id)
    await update.message.reply_text(
        f"🎉 Premium aktif {DAYS} hari! Berlaku sampai {_fmt_tanggal(sampai)}.\n"
        "Makasih banyak 🙏"
    )


def register(app: Application, db):
    db.execute(PAYMENTS_SCHEMA)
    db.commit()
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern=r"^premium:buy:\d+$"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, paid_success))
    log.info("premium plugin registered")
