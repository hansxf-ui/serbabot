"""Plugin anonchat: ngobrol anonim 1 lawan 1 via Telegram."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import add_report, upsert_user
from .filtering import contains_badword, load_badwords
from .pairing import PairingManager

log = logging.getLogger("serbabot.anonchat")

pairing = PairingManager(timeout=600)  # 10 menit tanpa pesan -> putus
BADWORDS = load_badwords()

last_relay = {}  # sender_id -> (recipient_chat_id, message_id)
last_text = {}   # sender_id -> teks terakhir yang diteruskan

WELCOME = (
    "Halo! Gue SerbaBot.\n\n"
    "Di sini lu bisa ngobrol anonim sama orang random. "
    "Nama lu nggak kelihatan, yang kelihatan cuma obrolan.\n\n"
    "Pencet tombol di bawah buat mulai."
)

PREMIUM_TEXT = (
    "SerbaBot Premium\n\n"
    "Harga: Rp15.000/bulan\n"
    "Fitur: antrean prioritas, tanpa batas /next, badge premium.\n\n"
    "Cara bayar (manual, phase 1):\n"
    "1. Transfer ke QRIS/DANA (minta info ke admin)\n"
    "2. Kirim bukti transfer ke bot ini\n"
    "3. Admin verifikasi, akun lu jadi premium\n\n"
    "Verifikasi otomatis nyusul di phase berikutnya."
)


def _db(context):
    return context.bot_data["db"]


def _kb_search():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cari teman ngobrol", callback_data="search")]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(_db(context), user.id, user.username)
    await update.message.reply_text(WELCOME, reply_markup=_kb_search())


async def _search(user, reply, context):
    upsert_user(_db(context), user.id, user.username)
    status, partner = pairing.search(user.id)
    if status == "already":
        await reply("Lu lagi ngobrol nih. /next buat ganti orang, /stop buat berhenti.")
    elif status == "paired":
        msg = "Ketemu! Ngobrol gih. /next = ganti orang, /stop = berhenti."
        await reply(msg)
        await context.bot.send_message(chat_id=partner, text=msg)
    else:
        await reply("Lagi nyariin temen ngobrol... sabar ya. /stop buat batal.")


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _search(update.effective_user, update.message.reply_text, context)


async def search_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await _search(q.from_user, q.message.reply_text, context)


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    status, partner = pairing.stop(user.id)
    if status == "unpaired" and partner:
        await context.bot.send_message(
            chat_id=partner, text="Temen ngobrol lu cabut. /search buat cari lagi."
        )
    await _search(user, update.message.reply_text, context)


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    status, partner = pairing.stop(user.id)
    if status == "unpaired" and partner:
        await context.bot.send_message(
            chat_id=partner, text="Temen ngobrol lu cabut. /search buat cari lagi."
        )
        await update.message.reply_text("Sesi berakhir. /search kalau mau ngobrol lagi.")
    elif status == "dequeued":
        await update.message.reply_text("Antrean dibatalkan.")
    else:
        await update.message.reply_text("Lu lagi nggak ngobrol sama siapa-siapa.")


async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text or ""
    upsert_user(_db(context), user.id, user.username)
    partner = pairing.get_partner(user.id)
    if partner is None:
        await update.message.reply_text("Belum nyambung ke siapa-siapa. /search dulu gih.")
        return
    if contains_badword(text, BADWORDS):
        await update.message.reply_text("Pesan lu kena filter kata kasar, nggak diterusin.")
        return
    pairing.touch(user.id)
    pairing.touch(partner)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Laporkan pesan ini", callback_data=f"report:{user.id}")]]
    )
    sent = await context.bot.send_message(chat_id=partner, text=text, reply_markup=kb)
    last_relay[user.id] = (partner, sent.message_id)
    last_text[user.id] = text


async def report_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    reporter = q.from_user
    try:
        reported_id = int(q.data.split(":", 1)[1])
    except (IndexError, ValueError):
        return
    if pairing.get_partner(reporter.id) != reported_id:
        await q.message.reply_text("Sesi udah berakhir, nggak bisa lapor.")
        return
    admin_id = context.bot_data["admin_id"]
    text = last_text.get(reported_id, "")
    add_report(_db(context), reporter.id, reported_id, text)
    info = last_relay.get(reported_id)
    if info:
        chat_id, msg_id = info
        try:
            await context.bot.forward_message(
                chat_id=admin_id, from_chat_id=chat_id, message_id=msg_id
            )
        except Exception as e:  # noqa: BLE001 - jangan matikan bot gara2 forward gagal
            log.warning("forward ke admin gagal: %s", e)
    await context.bot.send_message(
        chat_id=admin_id,
        text=f"Laporan\nDari: {reporter.id}\nTerlapor: {reported_id}\nPesan: {text[:500]}",
    )
    await q.message.reply_text("Laporan diterima. Makasih udah bantu jaga!")


async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PREMIUM_TEXT)


async def timeout_job(context: ContextTypes.DEFAULT_TYPE):
    for user_id in pairing.expired():
        status, partner = pairing.stop(user_id)
        if status == "unpaired" and partner:
            msg = "Sesi diputus otomatis: 10 menit nggak ada kabar. /search buat ngobrol lagi."
            for chat_id in (user_id, partner):
                try:
                    await context.bot.send_message(chat_id=chat_id, text=msg)
                except Exception as e:  # noqa: BLE001
                    log.warning("notif timeout gagal: %s", e)


def register(app: Application, db):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("next", next_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CallbackQueryHandler(search_btn, pattern="^search$"))
    app.add_handler(CallbackQueryHandler(report_btn, pattern=r"^report:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay))
    app.job_queue.run_repeating(timeout_job, interval=60, first=60)
    log.info("anonchat plugin registered")
