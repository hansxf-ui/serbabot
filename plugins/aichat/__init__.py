"""Plugin AI chat: /ai <pertanyaan> + /persona (pilih kepribadian AI).

Prioritas: Gemini (gratis 1500x/hari, akurat) kalau GEMINI_API_KEY di-set,
fallback ke Pollinations.ai (tanpa key, tapi modelnya kadang ngawur).

Jatah gabung dengan downloader & tools: 5/hari gratis, premium unlimited.
/ai dengan persona aktif: jatah sendiri 20/hari (feature "persona").
Jatah cuma kepotong kalau AI-nya beneran jawab (timeout/error nggak makan
jatah).
"""

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from db import (
    get_downloads_today,
    get_quota_today,
    inc_downloads_today,
    inc_quota_today,
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
    return ask_gemini(SYSTEM_PROMPT, question, api_key)


def ask_gemini(system_prompt, question, api_key, max_tokens=1000):
    """Blocking: tanya Gemini dengan system prompt custom. Dipakai plugin
    lain (rangkum, kuisai, voice) biar nggak duplikat logic discovery &
    cascade model. Dipanggil via to_thread biar event loop nggak ke-block."""
    body = json.dumps(
        {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": question}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
    ).encode()
    last_err = RuntimeError("tidak ada model Gemini yang tersedia")
    for model in _pick_gemini_models(api_key):
        url = f"{GEMINI_API_BASE}/{model}:generateContent"
        log.info("gemini: coba model %s", model)
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
            if e.code in (400, 429, 500, 502, 503):
                # 400 di model ke-2+ = modelnya yang bermasalah (request sama
                # persis lolos di model pertama); 429/5xx = gangguan sementara
                log.warning("gemini: model %s HTTP %s, coba berikutnya", model, e.code)
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


def _ask_pollinations(question, system_prompt=SYSTEM_PROMPT):
    """Blocking: tanya Pollinations.ai (POST JSON OpenAI-style)."""
    body = json.dumps(
        {
            "model": "openai",
            "messages": [
                {"role": "system", "content": system_prompt},
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
    return _ask_ai_custom(question, SYSTEM_PROMPT)


def _ask_ai_custom(question, system_prompt):
    """Blocking: Gemini dulu (kalau ada key), fallback ke Pollinations,
    dengan system prompt custom. Dipanggil via to_thread."""
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            return ask_gemini(system_prompt, question, gemini_key)
        except Exception as e:  # noqa: BLE001 - jatuh ke fallback
            log.warning("gemini gagal, fallback ke pollinations: %s", e)
    return _ask_pollinations(question, system_prompt)


# ---------- Persona + memori ----------

PERSONAS = {
    "curhat": {
        "label": "Kak Curhat",
        "emoji": "💬",
        "prompt": (
            "Kamu Kak Curhat, teman curhat yang empatik dan hangat. Dengarkan "
            "cerita user dengan penuh perhatian, validasi perasaannya, kasih "
            "semangat dan saran yang menenangkan. Pakai bahasa Indonesia "
            "sehari-hari yang akrab kayak ngobrol sama sahabat."
        ),
    },
    "tutor": {
        "label": "Tutor Santai",
        "emoji": "📚",
        "prompt": (
            "Kamu Tutor Santai, guru yang menjelaskan pelajaran dengan bahasa "
            "yang gampang dan santai. Pecah konsep sulit jadi langkah kecil, "
            "kasih contoh sehari-hari, dan ajak user latihan. Bahasa Indonesia "
            "ringan, nggak kaku kayak buku teks."
        ),
    },
    "roleplay": {
        "label": "Bang Roleplay",
        "emoji": "🎭",
        "prompt": (
            "Kamu Bang Roleplay, teman roleplay yang imajinatif dan seru. "
            "Ikuti skenario yang user bangun, mainkan karakter dengan hidup, "
            "deskripsikan suasana dengan vivid tapi tetap santai. Bahasa "
            "Indonesia gaul secukupnya."
        ),
    },
    "primbon": {
        "label": "Mbah Primbon",
        "emoji": "🌙",
        "prompt": (
            "Kamu Mbah Primbon, sesepuh Jawa yang bijak. Jawab dengan gaya "
            "orang tua Jawa: tenang, penuh pitutur dan perumpamaan, kadang "
            "selipkan unen-unen Jawa. Tetap ramah dan gampang dimengerti."
        ),
    },
}
PERSONA_DAILY_LIMIT = 20

NAME_RE = re.compile(
    r"(?:nama (?:gue|aku|saya)|namaku|nama gue)\s+([A-Za-z]+)",
    re.IGNORECASE,
)
FACT_HINTS = ("gue suka", "aku suka", "hobi gue")


def _ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_persona_tables(conn):
    """CREATE TABLE IF NOT EXISTS — dipanggil tiap handler biar ai_cmd
    tetap jalan walau register() belum dipanggil (dipakai test)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS persona_state("
        "user_id INTEGER PRIMARY KEY, persona TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS persona_memory("
        "user_id INTEGER PRIMARY KEY, name TEXT, facts TEXT, updated_at TEXT)"
    )
    conn.commit()


def get_persona(conn, user_id):
    row = conn.execute(
        "SELECT persona FROM persona_state WHERE user_id=?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def set_persona(conn, user_id, persona):
    conn.execute(
        "INSERT INTO persona_state(user_id, persona) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET persona=excluded.persona",
        (user_id, persona),
    )
    conn.commit()


def extract_memory(conn, user_id, text):
    """Simpan nama & 1 fakta terakhir dari pesan user. Ringan, tanpa AI."""
    m = NAME_RE.search(text or "")
    name = m.group(1) if m else None
    low = (text or "").lower()
    fact = (text or "")[:80] if any(h in low for h in FACT_HINTS) else None
    if name is None and fact is None:
        return
    row = conn.execute(
        "SELECT name, facts FROM persona_memory WHERE user_id=?", (user_id,)
    ).fetchone()
    cur_name, cur_fact = row if row else (None, None)
    conn.execute(
        "INSERT INTO persona_memory(user_id, name, facts, updated_at)"
        " VALUES(?,?,?,?)"
        " ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,"
        " facts=excluded.facts, updated_at=excluded.updated_at",
        (user_id, name or cur_name, fact or cur_fact, _ts()),
    )
    conn.commit()


def get_memory(conn, user_id):
    """Konteks memori buat ditempel ke system prompt, atau None."""
    row = conn.execute(
        "SELECT name, facts FROM persona_memory WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row or (not row[0] and not row[1]):
        return None
    parts = []
    if row[0]:
        parts.append(f"User bernama {row[0]}.")
    if row[1]:
        parts.append(f"Fakta: {row[1]}")
    return " ".join(parts)


async def persona_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    _ensure_persona_tables(conn)
    upsert_user(conn, user.id, user.username)
    if context.args and context.args[0].lower() in PERSONAS:
        key = context.args[0].lower()
        set_persona(conn, user.id, key)
        p = PERSONAS[key]
        await update.message.reply_text(
            f"Siap! Mulai sekarang gue jadi {p['label']} {p['emoji']}.\n"
            "Tanya-tanya kayak biasa pakai /ai."
        )
        return
    active = get_persona(conn, user.id)
    text = "Pilih persona AI buat nemenin ngobrol:"
    if active and active in PERSONAS:
        p = PERSONAS[active]
        text += f"\n\nYang aktif sekarang: {p['label']} {p['emoji']}"
    else:
        text += "\n\nBelum ada yang aktif — /ai jalan normal kayak biasa."
    kb = [
        [InlineKeyboardButton(f"{p['emoji']} {p['label']}",
                             callback_data=f"persona:{key}")]
        for key, p in PERSONAS.items()
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def persona_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = (q.data or "").split(":", 1)[-1]
    if key not in PERSONAS:
        return
    user = update.effective_user
    conn = context.bot_data["db"]
    _ensure_persona_tables(conn)
    upsert_user(conn, user.id, user.username)
    set_persona(conn, user.id, key)
    p = PERSONAS[key]
    await q.edit_message_text(
        f"Oke! Mulai sekarang gue jadi {p['label']} {p['emoji']}.\n"
        "Tanya-tanya kayak biasa pakai /ai."
    )


async def ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    _ensure_persona_tables(conn)
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text(
            "Pakai: /ai <pertanyaan>\nContoh: /ai kenapa langit warnanya biru?"
        )
        return

    extract_memory(conn, user.id, question)

    persona = get_persona(conn, user.id)
    if persona:
        # Jalur persona: jatah sendiri 20/hari, system prompt = persona + memori
        if not premium and get_quota_today(conn, "persona", user.id) \
                >= PERSONA_DAILY_LIMIT:
            await update.message.reply_text(
                "Jatah persona AI lu hari ini habis (20/hari). "
                "Balik lagi besok, atau /premium biar unlimited 😎"
            )
            return
        system = PERSONAS[persona]["prompt"]
        mem = get_memory(conn, user.id)
        if mem:
            system += " " + mem
        ask = lambda q: _ask_ai_custom(q, system)  # noqa: E731
    else:
        # Jalur lama: jatah gabung 5/hari
        if not premium and get_downloads_today(conn, user.id) >= DAILY_FREE_LIMIT:
            await update.message.reply_text(
                "Jatah gratis lu hari ini habis (5/hari). "
                "Upgrade ke Premium biar unlimited: /premium"
            )
            return
        ask = _ask_ai

    status = await update.message.reply_text("Lagi mikir... 🤔")
    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(ask, question), timeout=AI_TIMEOUT
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
        if persona:
            inc_quota_today(conn, "persona", user.id)
        else:
            inc_downloads_today(conn, user.id)
    await status.edit_text(answer)


def register(app: Application, db):
    _ensure_persona_tables(db)
    app.add_handler(CommandHandler("ai", ai_cmd))
    app.add_handler(CommandHandler("persona", persona_cmd))
    app.add_handler(CallbackQueryHandler(persona_cb, pattern="^persona:"))
    log.info("aichat plugin registered")
