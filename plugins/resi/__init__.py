"""Plugin cek resi multi-ekspedisi: /resi <nomor>.

JUJUR: nggak ada API cek resi gratis yang andal buat Indonesia. Plugin ini
cuma jalan kalau admin pasang RESI_API_KEY (agregator berbayar, mis.
BinderByte). Tanpa key → balas jujur, nggak fake data, nggak makan jatah.

Env:
  RESI_PROVIDER  "binderbyte" (default) | "custom"
  RESI_API_KEY   api key agregator
  RESI_API_URL   (cuma buat provider "custom")

Jatah: 3/hari (feature "resi"), VIP unlimited. Jatah cuma kepotong kalau
provider beneran dipanggil — pesan konfigurasi/validasi nggak makan jatah.
"""
import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import (
    get_quota_today,
    inc_quota_today,
    is_vip,
    upsert_user,
)

log = logging.getLogger("serbabot.resi")

RESI_LIMIT = 3
RESI_TIMEOUT = 15  # detik per request
MAX_COURIER_TRIES = 3
MAX_HISTORY = 8
MAX_RAW_LINES = 10
MAX_REPLY = 3500

AWB_RE = re.compile(r"^[A-Za-z0-9]{8,30}$")

BINDERBYTE_API = "https://api.binderbyte.com/v1/track"
# Daftar courier binderbyte (docs.binderbyte.com/api/cek-resi); dicoba
# satu per satu maks MAX_COURIER_TRIES biar nggak lama.
COURIERS = ["jne", "jnt", "sicepat", "gosend", "anteraja", "pos"]


class ResiError(Exception):
    pass


def _http_get_json(url, headers=None, timeout=RESI_TIMEOUT):
    """Blocking: GET → parsed JSON. Dipanggil via to_thread."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _fetch_binderbyte(api_key, nomor):
    """BinderByte: coba courier satu per satu sampai status 200 + data
    valid. Raise ResiError kalau semua gagal."""
    tried = []
    for courier in COURIERS[:MAX_COURIER_TRIES]:
        qs = urllib.parse.urlencode(
            {"api_key": api_key, "courier": courier, "awb": nomor}
        )
        url = f"{BINDERBYTE_API}?{qs}"
        tried.append(courier)
        try:
            payload = _http_get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                log.info("binderbyte: courier %s 404, coba berikutnya", courier)
                continue
            raise ResiError(f"binderbyte error HTTP {e.code}") from e
        except Exception as e:  # noqa: BLE001 - network blip → balasan ramah
            raise ResiError(f"binderbyte tidak bisa dihubungi: {e}") from e
        data = (payload or {}).get("data") or {}
        summary = data.get("summary") or {}
        if payload.get("status") == 200 and data and summary.get("awb"):
            history = [
                (h.get("date", ""), h.get("desc", ""))
                for h in (data.get("history") or [])
                if isinstance(h, dict)
            ]
            return {
                "courier": summary.get("courier") or courier.upper(),
                "awb": summary.get("awb"),
                "status": summary.get("status") or "tidak diketahui",
                "history": history[:MAX_HISTORY],
            }
        # status != 200 (mis. awb salah) atau data invalid → coba kurir lain
        log.info("binderbyte: courier %s status=%s, coba berikutnya",
                 courier, payload.get("status"))
    raise ResiError(
        "Resi nggak ketemu di " + ", ".join(tried) + ". Cek nomornya bener nggak 🙏"
    )


def _fetch_custom(api_key, api_url, nomor):
    """Agregator custom: GET <url>?awb=<nomor>, Bearer auth. Parse generik:
    ambil field status/history kalau ada, kalau nggak → JSON ringkas."""
    url = api_url + ("&" if "?" in api_url else "?") + "awb=" + urllib.parse.quote(nomor)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        payload = _http_get_json(url, headers=headers)
    except Exception as e:  # noqa: BLE001 - network blip → balasan ramah
        raise ResiError(f"agregator tidak bisa dihubungi: {e}") from e
    if not isinstance(payload, dict):
        raise ResiError("respon agregator nggak bisa diparse 🙏")
    status = payload.get("status") or (payload.get("summary") or {}).get("status")
    history = payload.get("history") or payload.get("trackings") or []
    parsed = []
    for h in history:
        if isinstance(h, dict):
            parsed.append((h.get("date") or h.get("time") or "",
                           h.get("desc") or h.get("description") or ""))
        elif isinstance(h, str):
            parsed.append(("", h))
    if status is None and not parsed:
        # Format asing → tampilkan JSON ringkas apa adanya, jujur.
        raw = json.dumps(payload, indent=2, ensure_ascii=False).splitlines()
        return {
            "courier": "custom", "awb": nomor, "status": "?", "history": [],
            "raw": "\n".join(raw[:MAX_RAW_LINES]),
        }
    return {
        "courier": payload.get("courier") or "custom",
        "awb": payload.get("awb") or nomor,
        "status": str(status or "tidak diketahui"),
        "history": parsed[:MAX_HISTORY],
    }


def fetch_resi(provider, api_key, nomor):
    """Ambil status resi. Return dict(courier, awb, status, history,
    raw?). Raise ResiError kalau gagal — jangan pernah fake data."""
    provider = (provider or "binderbyte").lower()
    if provider == "binderbyte":
        return _fetch_binderbyte(api_key, nomor)
    if provider == "custom":
        api_url = os.environ.get("RESI_API_URL", "").strip()
        if not api_url:
            raise ResiError("RESI_API_URL belum di-set 🙏")
        return _fetch_custom(api_key, api_url, nomor)
    raise ResiError(f"provider '{provider}' nggak dikenal 🙏")


def format_resi(res):
    """dict dari fetch_resi → teks balasan Telegram."""
    lines = [
        f"📦 {res['courier']} — {res['awb']}",
        f"Status: {res['status']}",
    ]
    if res.get("raw"):
        lines += ["", res["raw"]]
    elif res["history"]:
        lines += ["", "Riwayat:"]
        lines += [f"• {tgl} — {desc}".strip(" —") for tgl, desc in res["history"]]
    else:
        lines += ["", "Belum ada riwayat perjalanan."]
    text = "\n".join(lines)
    return text[:MAX_REPLY] + "..." if len(text) > MAX_REPLY else text


def _config():
    provider = os.environ.get("RESI_PROVIDER", "binderbyte").strip() or "binderbyte"
    api_key = os.environ.get("RESI_API_KEY", "").strip()
    return provider, api_key


async def resi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    premium = is_vip(conn, user.id, context.bot_data.get("admin_id"))

    nomor = " ".join(context.args).strip().replace(" ", "")
    if not AWB_RE.match(nomor):
        await update.message.reply_text(
            "Pakai: /resi <nomor resi>\nContoh: /resi JP6961181926"
        )
        return

    if not premium and get_quota_today(conn, "resi", user.id) >= RESI_LIMIT:
        await update.message.reply_text(
            "Jatah cek resi lu hari ini habis (3/hari). "
            "Balik lagi besok, atau /premium biar unlimited 😎"
        )
        return

    provider, api_key = _config()
    if not api_key:
        # JUJUR: tanpa key nggak bisa apa-apa. Nggak panggil network,
        # nggak makan jatah.
        await update.message.reply_text(
            "Fitur cek resi butuh API key agregator (berbayar). "
            "Minta admin setting RESI_API_KEY dulu 🙏"
        )
        return

    status = await update.message.reply_text("Lagi lacak paketnya... 📦")
    try:
        res = await asyncio.wait_for(
            asyncio.to_thread(fetch_resi, provider, api_key, nomor),
            timeout=RESI_TIMEOUT * (MAX_COURIER_TRIES + 1),
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("resi timeout: %s", nomor)
        await status.edit_text("Lacak resinya kelamaan. Coba lagi nanti 🙏")
        return
    except ResiError as e:
        log.warning("resi gagal: %s", e)
        err_msg = str(e)
    else:
        if not premium:
            inc_quota_today(conn, "resi", user.id)
        await status.edit_text(format_resi(res))
        return
    await status.edit_text(err_msg)
    # Provider sempat dipanggil tapi gagal → tetap makan jatah (proteksi
    # rate-limit), kecuali error murni konfigurasi (belum ada RESI_API_URL).
    if "RESI_API_URL" not in err_msg and not premium:
        inc_quota_today(conn, "resi", user.id)


def register(app: Application, db):
    app.add_handler(CommandHandler("resi", resi_cmd))
    log.info("resi plugin registered")
