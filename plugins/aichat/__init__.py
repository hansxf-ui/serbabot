"""Plugin AI chat: /ai <pertanyaan>.

Prioritas: Gemini (gratis 1500x/hari, akurat) kalau GEMINI_API_KEY di-set,
fallback ke Pollinations.ai (tanpa key, tapi modelnya kadang ngawur).

Jatah gabung dengan downloader & tools: 5/hari gratis, premium unlimited.
Jatah cuma kepotong kalau AI-nya beneran jawab (timeout/error nggak makan
jatah).
"""

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import (
    get_downloads_today,
    inc_downloads_today,
    is_vip,
    upsert_user,
)

log = logging.getLogger("serbabot.aichat")

DAILY_FREE_LIMIT = 5
AI_TIMEOUT = 60  # detik
POLL_API = "https://text.pollinations.ai/"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Cascade kalau auto-discovery gagal (mis. network blip).
GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]
MAX_REPLY = 3500  # Telegram max 4096; potong biar aman

_GEMINI_MODEL_CACHE = None  # hasil discovery, di-cache per proses

SYSTEM_PROMPT = (
    "Kamu asisten santai berbahasa Indonesia. Jawab pakai bahasa sehari-hari "
    "yang gampang dimengerti, singkat tapi jelas. Jangan pakai bahasa kaku. "
    "Kalau user minta dibuatkan gambar, bilang aja pakai perintah /gambar."
)


def _discover_gemini_models(api_key):
    """GET /v1beta/models → nama model yang support generateContent.

    Google mempensiunkan model lama (kasus: gemini-2.0-flash → 404), jadi
    daftar model diambil live, bukan hardcode. Hasil di-cache per proses.
    """
    global _GEMINI_MODEL_CACHE
    if _GEMINI_MODEL_CACHE is not None:
        return _GEMINI_MODEL_CACHE
    req = urllib.request.Request(
        GEMINI_API_BASE,
        headers={"x-goog-api-key": api_key},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))
    names = []
    for m in payload.get("models", []):
        name = m.get("name") or ""
        if name.startswith("models/"):
            name = name[len("models/"):]
        methods = m.get("supportedGenerationMethods") or []
        if name and "generateContent" in methods:
            names.append(name)
    _GEMINI_MODEL_CACHE = names
    log.info("gemini: discovery ketemu %d model", len(names))
    return names


def _pick_gemini_models(api_key):
    """Urutan model yang dicoba: flash stabil versi tertinggi dulu
    (dari discovery), lalu cascade hardcoded kalau discovery gagal."""
    try:
        discovered = _discover_gemini_models(api_key)
    except Exception as e:  # noqa: BLE001 - discovery opsional
        log.warning("gemini: discovery model gagal: %s", e)
        discovered = []
    flash = [
        n for n in discovered
        if "flash" in n.lower()
        and not any(x in n.lower() for x in ("preview", "exp"))
    ]

    def sort_key(n):
        m = re.search(r"(\d+)\.(\d+)", n)
        ver = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        lite = 0 if "lite" not in n.lower() else 1
        return (ver[0], ver[1], -lite)

    flash.sort(key=sort_key, reverse=True)
    ordered, seen = [], set()
    for n in flash + GEMINI_FALLBACK_MODELS:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def _ask_gemini(question, api_key):
    """Blocking: tanya Gemini (REST, tanpa SDK). Coba model satu per satu;
    yang 404 (dipensiunkan) di-skip. Raise kalau semua gagal."""
    body = json.dumps(
        {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": question}]}],
            "generationConfig": {"maxOutputTokens": 1000},
        }
    ).encode()
    last_err = RuntimeError("tidak ada model Gemini yang tersedia")
    for model in _pick_gemini_models(api_key):
        url = f"{GEMINI_API_BASE}/{model}:generateContent"
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.warning("gemini: model %s 404 (pensiun?), coba berikutnya", model)
                last_err = e
                continue
            raise
        cands = payload.get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts") or [] if cands else []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise RuntimeError("Gemini: jawaban kosong")
        log.info("gemini: OK via model %s", model)
        return text
    raise last_err


def _ask_pollinations(question):
    """Blocking: tanya Pollinations.ai (POST JSON OpenAI-style)."""
    body = json.dumps(
        {
            "model": "openai",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        POLL_API,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace").strip()


def _ask_ai(question):
    """Blocking: Gemini dulu (kalau ada key), fallback ke Pollinations.
    Dipanggil via to_thread biar event loop nggak ke-block."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            return _ask_gemini(question, gemini_key)
        except Exception as e:  # noqa: BLE001 - jatuh ke fallback
            log.warning("gemini gagal, fallback ke pollinations: %s", e)
    return _ask_pollinations(question)


async def ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(
            "Pakai: /ai <pertanyaan>\nContoh: /ai kenapa langit warnanya biru?"
        )
        return

    if not premium and get_downloads_today(conn, user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah gratis lu hari ini habis (5/hari). "
            "Upgrade ke Premium biar unlimited: /premium"
        )
        return

    status = await update.message.reply_text("Lagi mikir... 🤔")
    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(_ask_ai, question), timeout=AI_TIMEOUT
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("ai timeout")
        await status.edit_text("AInya kelamaan mikir. Coba lagi nanti 🙏")
        return
    except Exception as e:  # noqa: BLE001 - API mati harus jadi pesan ramah
        log.warning("ai gagal: %s", e)
        await status.edit_text("AInya lagi error. Coba lagi nanti 🙏")
        return

    if not answer:
        await status.edit_text("AInya nggak jawab. Coba tanya yang lain 🙏")
        return
    if len(answer) > MAX_REPLY:
        answer = answer[:MAX_REPLY] + "..."
    if not premium:
        inc_downloads_today(conn, user.id)
    await status.edit_text(answer)


def register(app: Application, db):
    app.add_handler(CommandHandler("ai", ai_cmd))
    log.info("aichat plugin registered")
