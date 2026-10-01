"""Plugin Truth or Dare Indonesia.

/tod — pilih Truth atau Dare via tombol, dapat 1 kartu acak.
10x/hari gratis, premium unlimited. Jalan di grup & private.
"""
import json
import logging
import os
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from db import get_quota_today, inc_quota_today, is_vip, upsert_user

log = logging.getLogger("serbabot.tod")

DAILY_FREE_LIMIT = 10
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deck.json")

with open(_DATA, encoding="utf-8") as f:
    DECK = json.load(f)

_TRUTH = [c for c in DECK if c["type"] == "truth"]
_DARE = [c for c in DECK if c["type"] == "dare"]
_CAT_EMOJI = {"kocak": "😂", "baper": "🥺", "berani": "😈"}

MENU = InlineKeyboardMarkup([[
    InlineKeyboardButton("🎲 Truth", callback_data="tod:truth"),
    InlineKeyboardButton("🔥 Dare", callback_data="tod:dare"),
]])


def register(app: Application, db):
    app.add_handler(CommandHandler("tod", tod_cmd))
    app.add_handler(CallbackQueryHandler(tod_cb, pattern=r"^tod:"))
    log.info("tod plugin registered")


async def tod_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(context.bot_data["db"], user.id, user.username)
    await update.message.reply_text(
        "Truth or Dare? Pilih nasib lu 😏", reply_markup=MENU
    )


async def tod_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    await q.answer()
    if not is_vip(conn, user.id, context.bot_data.get("admin_id")) \
            and get_quota_today(conn, "tod", user.id) >= DAILY_FREE_LIMIT:
        await q.edit_message_text(
            "Jatah gratis lu hari ini habis (10/hari). "
            "Balik lagi besok, atau /premium biar unlimited 😎"
        )
        return
    kind = q.data.split(":", 1)[1]
    card = random.choice(_TRUTH if kind == "truth" else _DARE)
    emoji = _CAT_EMOJI.get(card["category"], "🎴")
    title = "TRUTH 🎲" if kind == "truth" else "DARE 🔥"
    inc_quota_today(conn, "tod", user.id)
    await q.edit_message_text(
        f"{title} {emoji}\n\n{card['text']}\n\nMau lagi? /tod",
        reply_markup=MENU,
    )
