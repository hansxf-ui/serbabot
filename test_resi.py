"""Test plugin resi: validasi, binderbyte (mock), fallback courier, custom,
env kosong → jujur tanpa network/quota, quota 3/hari.

Semua di-mock (urllib + Telegram). Tidak ada network sungguhan.
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.resi as resi

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester"


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn, args):
        self.args = args
        self.bot_data = {"db": conn}


# ---------- fake HTTP ----------

class FakeResp:
    def __init__(self, text):
        self._t = text

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


BB_OK = json.dumps({
    "status": 200,
    "message": "Successfully tracked package",
    "data": {
        "summary": {
            "awb": "JP6961181926",
            "courier": "J&T Express",
            "service": "",
            "status": "DELIVERED",
            "date": "2020-10-01 12:30:28",
        },
        "detail": {"origin": "", "destination": "", "shipper": "", "receiver": ""},
        "history": [
            {"date": "2020-10-01 12:30:28", "desc": "TERKIRIM: DROP POINT",
             "location": "SUNGAI PENUH"},
            {"date": "2020-10-01 09:04:16", "desc": "SEDANG DIANTAR: DROP POINT",
             "location": "SUNGAI PENUH"},
        ],
    },
})
BB_404 = json.dumps({"status": 404, "message": "Invalid awb or courier"})

http_calls = []
http_headers = {}


def set_fake(handler):
    urllib.request.urlopen = handler


def fake_binderbyte_jne_ok(req, timeout=None):
    http_calls.append(req.full_url)
    return FakeResp(BB_OK if "courier=jne" in req.full_url else BB_404)


real_urlopen = urllib.request.urlopen

print("== resi ==")
conn = db.init_db(":memory:")
os.environ["RESI_API_KEY"] = "TESTKEY"

# 1. tanpa argumen -> contoh pakai, tanpa HTTP, tanpa quota
set_fake(fake_binderbyte_jne_ok)
upd1 = FakeUpdate(101)
asyncio.run(resi.resi_cmd(upd1, FakeContext(conn, [])))
_t("tanpa nomor: kasih contoh pakai",
   any("/resi <nomor resi>" in r for r in upd1.message.replies))
_t("tanpa nomor: tidak panggil HTTP", http_calls == [])
_t("tanpa nomor: jatah tidak kepotong",
   db.get_quota_today(conn, "resi", 101) == 0)

# 1b. nomor invalid (simbol, kependekan, kepanjangan) -> contoh pakai
http_calls.clear()
updbs = []
for bad in ["ABC!!", "ab", "x" * 31]:
    updb = FakeUpdate(102)
    asyncio.run(resi.resi_cmd(updb, FakeContext(conn, [bad])))
    updbs.append(updb)
_t("nomor invalid: kasih contoh pakai",
   all(any("/resi <nomor resi>" in r for r in u.message.replies) for u in updbs))
_t("nomor invalid: tidak panggil HTTP", http_calls == [])

# 2. binderbyte OK: courier jne, format benar, jatah kepotong 1
http_calls.clear()
upd2 = FakeUpdate(111)
asyncio.run(resi.resi_cmd(upd2, FakeContext(conn, ["JP6961181926"])))
_t("binderbyte: request ke api.binderbyte.com/v1/track",
   any("api.binderbyte.com/v1/track" in u for u in http_calls))
_t("binderbyte: api_key kekirim", any("api_key=TESTKEY" in u for u in http_calls))
_t("binderbyte: courier jne dicoba", any("courier=jne" in u for u in http_calls))
msg2 = upd2.message.edits[-1] if upd2.message.edits else ""
_t("format: header courier — awb",
   msg2.startswith("📦 J&T Express — JP6961181926"))
_t("format: status", "\nStatus: DELIVERED" in msg2)
_t("format: riwayat 2 baris",
   "2020-10-01 12:30:28 — TERKIRIM: DROP POINT" in msg2
   and "2020-10-01 09:04:16 — SEDANG DIANTAR: DROP POINT" in msg2)
_t("sukses: jatah kepotong 1", db.get_quota_today(conn, "resi", 111) == 1)

# 3. fallback courier: jne 404 → coba jnt → OK
calls3 = []


def fake_fallback(req, timeout=None):
    calls3.append(req.full_url)
    if "courier=jne" in req.full_url:
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    return FakeResp(BB_OK)


set_fake(fake_fallback)
upd3 = FakeUpdate(112)
asyncio.run(resi.resi_cmd(upd3, FakeContext(conn, ["JP6961181926"])))
_t("fallback: jne dicoba dulu", any("courier=jne" in u for u in calls3))
_t("fallback: 404 → jnt dicoba", any("courier=jnt" in u for u in calls3))
_t("fallback: maks 3 percobaan", len(calls3) <= 3)
_t("fallback: jawaban dari kurir pengganti sampai",
   any("DELIVERED" in e for e in upd3.message.edits))

# 4. provider custom: parse generik (status + history)
os.environ["RESI_PROVIDER"] = "custom"
os.environ["RESI_API_URL"] = "https://agg.example.com/track"


def fake_custom(req, timeout=None):
    http_calls.append(req.full_url)
    http_headers.update(req.header_items())
    return FakeResp(json.dumps({
        "courier": "AnterAja",
        "awb": "1000012345",
        "status": "ON DELIVERY",
        "history": [
            {"date": "2026-10-01 08:00", "desc": "Paket keluar dari hub"},
        ],
    }))


http_calls.clear()
set_fake(fake_custom)
upd4 = FakeUpdate(113)
asyncio.run(resi.resi_cmd(upd4, FakeContext(conn, ["1000012345"])))
_t("custom: URL + awb param", any("https://agg.example.com/track?awb=1000012345" in u
                                 for u in http_calls))
_t("custom: Bearer auth", http_headers.get("Authorization") == "Bearer TESTKEY")
msg4 = upd4.message.edits[-1] if upd4.message.edits else ""
_t("custom: status + riwayat terparse",
   msg4.startswith("📦 AnterAja — 1000012345")
   and "Status: ON DELIVERY" in msg4
   and "2026-10-01 08:00 — Paket keluar dari hub" in msg4)
del os.environ["RESI_PROVIDER"]
del os.environ["RESI_API_URL"]

# 5. custom dengan format asing → JSON ringkas (maks 10 baris), jujur
os.environ["RESI_PROVIDER"] = "custom"
os.environ["RESI_API_URL"] = "https://agg.example.com/track"


def fake_custom_raw(req, timeout=None):
    http_calls.append(req.full_url)
    return FakeResp(json.dumps({f"field{i}": f"val{i}" for i in range(20)}))


http_calls.clear()
set_fake(fake_custom_raw)
res5 = resi.fetch_resi("custom", "K", "XYZ12345")
_t("custom asing: raw JSON ≤10 baris",
   "raw" in res5 and len(res5["raw"].splitlines()) <= 10)
_t("custom asing: jujur (bukan status ngarang)", res5["status"] == "?")
del os.environ["RESI_PROVIDER"]
del os.environ["RESI_API_URL"]

# 6. env kosong → jujur, TANPA network, TANPA makan quota
del os.environ["RESI_API_KEY"]
http_calls.clear()
set_fake(fake_binderbyte_jne_ok)
upd6 = FakeUpdate(114)
asyncio.run(resi.resi_cmd(upd6, FakeContext(conn, ["JP6961181926"])))
_t("tanpa key: pesan jujur minta admin setting RESI_API_KEY",
   any("butuh API key agregator" in r and "RESI_API_KEY" in r
       for r in upd6.message.replies))
_t("tanpa key: tidak panggil HTTP", http_calls == [])
_t("tanpa key: jatah tidak kepotong",
   db.get_quota_today(conn, "resi", 114) == 0)
os.environ["RESI_API_KEY"] = "TESTKEY"

# 7. quota 3/hari: 4x ditolak, HTTP tidak dipanggil
set_fake(fake_binderbyte_jne_ok)
for _ in range(3):
    db.inc_quota_today(conn, "resi", 115)
http_calls.clear()
upd7 = FakeUpdate(115)
asyncio.run(resi.resi_cmd(upd7, FakeContext(conn, ["JP6961181926"])))
_t("quota habis: ditolak", any("habis" in r for r in upd7.message.replies))
_t("quota habis: HTTP tidak dipanggil", http_calls == [])

# 8. gagal di provider (resi nggak ketemu) → pesan ramah, tetap makan jatah
def fake_all_404(req, timeout=None):
    http_calls.append(req.full_url)
    raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

http_calls.clear()
set_fake(fake_all_404)
upd8 = FakeUpdate(116)
asyncio.run(resi.resi_cmd(upd8, FakeContext(conn, ["JP6961181926"])))
_t("resi nggak ketemu: pesan ramah",
   any("nggak ketemu" in e for e in upd8.message.edits))
_t("provider dipanggil tapi gagal: jatah tetap kepotong",
   db.get_quota_today(conn, "resi", 116) == 1)

# 9. premium: unlimited, jatah tidak dicatat
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(117,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
http_calls.clear()
set_fake(fake_binderbyte_jne_ok)
upd9 = FakeUpdate(117)
asyncio.run(resi.resi_cmd(upd9, FakeContext(conn, ["JP6961181926"])))
_t("premium: tetap dilayani", any("DELIVERED" in e for e in upd9.message.edits))
_t("premium: jatah tidak dicatat",
   db.get_quota_today(conn, "resi", 117) == 0)

urllib.request.urlopen = real_urlopen
del os.environ["RESI_API_KEY"]

print("RESI " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
