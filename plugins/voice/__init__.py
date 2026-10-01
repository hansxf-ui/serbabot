"""Plugin voice: /vn <teks> (teks -> voice note) + transkrip VN masuk.

/vn pakai edge-tts (suara id-ID-ArdiNeural), tanpa API key.
Transkrip VN masuk pakai Gemini audio (inline_data audio/ogg),
butuh GEMINI_API_KEY — tanpa key dibalas jujur.

Jatah gabung: 3/hari (feature "voice"), VIP unlimited. Jatah cuma
kepotong kalau prosesnya beneran sukses (gagal -> jatah aman).
"""
import asyncio
import base64
import json
import logging
import os
import tempfile
import urllib.error
import urllib.request

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import (
    get_quota_today,
    inc_quota_today,
    is_vip,
    upsert_user,
)
from plugins.aichat import (
    AI_TIMEOUT,
    GEMINI_API_BASE,
    _pick_gemini_models,
)

log = logging.getLogger("serbabot.voice")

DAILY_FREE_LIMIT = 3
MAX_VN_CHARS = 500
VOICE_NAME = "id-ID-ArdiNeural"
TRANSCRIBE_PROMPT = "Transkrip audio berikut ke teks Bahasa Indonesia apa adanya."


async def tts_generate(text, path):
    """Async: generate voice Bahasa Indonesia via edge-tts, simpan ke path.

    edge-tts sudah async, jadi dipanggil langsung (bukan to_thread).
    Raise ImportError kalau modulnya belum diinstall.
    """
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE_NAME)
    await communicate.save(path)


def transcribe_voice(audio_bytes, api_key):
    """Blocking: transkrip audio via Gemini REST (inline_data audio/ogg).

    Testable (mock urllib). Raise kalau semua model gagal.
    Dipanggil via to_thread biar event loop nggak ke-block.
    """
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    body = json.dumps(
        {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": TRANSCRIBE_PROMPT},
                        {"inline_data": {"mime_type": "audio/ogg", "data": b64}},
                    ],
                }
            ],
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
            if e.code in (400, 404, 429, 500, 502, 503):
                log.warning("voice: model %s HTTP %s, coba berikutnya", model, e.code)
                last_err = e
                continue
            raise
        cands = payload.get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts") or [] if cands else []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise RuntimeError("Gemini: transkrip kosong")
        log.info("voice: transkrip OK via model %s", model)
        return text
    raise last_err


async def vn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    try:
        import edge_tts  # noqa: F401 - cuma cek ketersediaan
    except ImportError:
        await update.message.reply_text("Fitur voice belum aktif di server 🙏")
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Pakai: /vn <teks>\nContoh: /vn halo, apa kabar semuanya?")
        return
    if len(text) > MAX_VN_CHARS:
        await update.message.reply_text(
            f"Teksnya kepanjangan (maks {MAX_VN_CHARS} karakter) 🙏")
        return

    if not premium and get_quota_today(conn, "voice", user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah voice lu hari ini habis (3/hari). "
            "Upgrade ke Premium biar unlimited: /premium")
        return

    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        await tts_generate(text, path)
        await update.message.reply_voice(voice=open(path, "rb"))
    except Exception as e:  # noqa: BLE001 - TTS mati harus jadi pesan ramah
        log.warning("vn gagal: %s", e)
        await update.message.reply_text("Voicenya gagal dibikin. Coba lagi nanti 🙏")
        return
    finally:
        if os.path.exists(path):
            os.unlink(path)
    if not premium:
        inc_quota_today(conn, "voice", user.id)


async def voice_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        await update.message.reply_text(
            "Fitur transkrip butuh API key, minta admin setting 🙏")
        return

    if not premium and get_quota_today(conn, "voice", user.id) >= DAILY_FREE_LIMIT:
        await update.message.reply_text(
            "Jatah voice lu hari ini habis (3/hari). "
            "Upgrade ke Premium biar unlimited: /premium")
        return

    status = await update.message.reply_text("Lagi dengerin VN-nya... 🎧")
    tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    tmp.close()
    try:
        tg_file = await context.bot.get_file(update.message.voice.file_id)
        await tg_file.download_to_drive(tmp.name)
        with open(tmp.name, "rb") as f:
            audio = f.read()
        text = await asyncio.to_thread(transcribe_voice, audio, api_key)
    except Exception as e:  # noqa: BLE001 - download/AI mati -> pesan ramah
        log.warning("transkrip gagal: %s", e)
        await status.edit_text("Transkripnya gagal. Coba lagi nanti 🙏")
        return
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)

    if not premium:
        inc_quota_today(conn, "voice", user.id)
    await status.edit_text(f"📝 Isi VN-nya:\n{text}")


def register(app: Application, db):
    app.add_handler(CommandHandler("vn", vn_cmd))
    app.add_handler(MessageHandler(filters.VOICE, voice_in))
    log.info("voice plugin registered")
