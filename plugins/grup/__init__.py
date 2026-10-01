"""Plugin grup: welcome member baru, anti-spam, statistik mingguan.

/statistik — top 5 chat minggu ini (grup saja)
/help      — daftar fitur grup (grup saja; diam di private)

DEPLOY: butuh bot jadi ADMIN grup + privacy mode OFF (BotFather /setprivacy).
"""
import logging
import re
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from plugins.anonchat.filtering import contains_badword, load_badwords

log = logging.getLogger("serbabot.grup")

_LINK_RE = re.compile(r"https?://", re.IGNORECASE)
_WARN_COOLDOWN = 60  # detik antar peringatan per grup
_last_warn = {}  # chat_id -> time.monotonic()

_BADWORDS = None


def _badwords():
    global _BADWORDS
    if _BADWORDS is None:
        _BADWORDS = load_badwords()
    return _BADWORDS


def _week():
    return time.strftime("%Y-W%V")


async def _reply_safe(update, text):
    try:
        await update.message.reply_text(text)
    except Exception:
        log.exception("reply gagal")


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat = update.effective_chat
        for m in update.message.new_chat_members:
            nama = m.first_name or m.username or "teman"
            await update.message.reply_text(
                f"Halo {nama}! Selamat datang di {chat.title or 'grup ini'} 👋 "
                f"Ketik /help buat lihat yang gue bisa."
            )
    except Exception:
        log.exception("welcome gagal")


async def antispam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text or ""

    if contains_badword(text, _badwords()):
        try:
            await context.bot.delete_message(chat_id, update.message.message_id)
        except Exception:
            log.exception("hapus pesan badword gagal")
            return
        now = time.monotonic()
        if now - _last_warn.get(chat_id, 0) >= _WARN_COOLDOWN:
            _last_warn[chat_id] = now
            await _reply_safe(update, "⚠️ Jangan pakai kata kasar ya, pesannya gue hapus 🙏")
        return

    if _LINK_RE.search(text):
        try:
            member = await context.bot.get_chat_member(
                chat_id, update.effective_user.id
            )
            if member.status in ("administrator", "creator"):
                return
        except Exception:
            return  # fail-open: cek admin gagal -> jangan hapus
        try:
            await context.bot.delete_message(chat_id, update.message.message_id)
        except Exception:
            log.exception("hapus pesan link gagal")


async def count_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = context.bot_data["db"]
        conn.execute(
            "INSERT INTO grup_stats(chat_id, user_id, week, count) VALUES(?,?,?,1) "
            "ON CONFLICT(chat_id, user_id, week) DO UPDATE SET count=count+1",
            (update.effective_chat.id, update.effective_user.id, _week()),
        )
        conn.commit()
    except Exception:
        log.exception("catat statistik gagal")


async def statistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    try:
        conn = context.bot_data["db"]
        rows = conn.execute(
            "SELECT user_id, count FROM grup_stats "
            "WHERE chat_id=? AND week=? ORDER BY count DESC LIMIT 5",
            (update.effective_chat.id, _week()),
        ).fetchall()
        if not rows:
            await _reply_safe(update, "Belum ada data chat minggu ini 🤷")
            return
        lines = ["🏆 Top chat minggu ini:"]
        for i, (uid, c) in enumerate(rows, 1):
            row = conn.execute(
                "SELECT username FROM users WHERE user_id=?", (uid,)
            ).fetchone()
            nama = "@" + row[0] if row and row[0] else f"user{uid}"
            lines.append(f"{i}. {nama} — {c} pesan")
        await _reply_safe(update, "\n".join(lines))
    except Exception:
        log.exception("statistik gagal")


async def grup_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    await _reply_safe(
        update,
        "🤖 Fitur grup:\n"
        "• Welcome member baru 👋\n"
        "• Anti-spam: kata kasar & link non-admin auto-hapus 🛡️\n"
        "• /statistik — top 5 chat minggu ini 🏆",
    )


def register(app: Application, db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS grup_stats("
        "chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, week TEXT NOT NULL, "
        "count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (chat_id, user_id, week))"
    )
    db.commit()
    app.add_handler(CommandHandler("statistik", statistik))
    app.add_handler(CommandHandler("help", grup_help))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome)
    )
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, antispam))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, count_stat))
    log.info("grup plugin registered")
