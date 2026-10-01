#!/usr/bin/env python3
"""Unit test plugin downloader — tanpa network, tanpa token Telegram.

Menguji dengan fungsi DB asli (SQLite :memory:) + fungsi asli plugin:
- limit harian: 5x lolos, ke-6 ditolak
- reset di hari berbeda
- premium = unlimited
- deteksi URL di teks
- is_in_session dari anonchat (dipakai downloader biar nggak ganggu sesi)
"""
import re
import sys

sys.path.insert(0, ".")

import db
from plugins.downloader import URL_RE, quota_ok
from plugins.anonchat import is_in_session, pairing

passed = 0


def check(name, cond):
    global passed
    status = "PASS" if cond else "FAIL"
    print(f"  {status} {name}")
    if cond:
        passed += 1
    else:
        raise AssertionError(name)


def fresh_db():
    conn = db.init_db(":memory:")
    return conn


print("== limit harian ==")
conn = fresh_db()
db.upsert_user(conn, 1, "gratisan")
check("user baru boleh download", quota_ok(conn, 1) is True)
for _ in range(5):
    db.inc_downloads_today(conn, 1, date="2026-10-01")
check("setelah 5x, jatah habis", quota_ok(conn, 1) is False)

print("== reset beda hari ==")
check(
    "hari berbeda jatah balik",
    db.get_downloads_today(conn, 1, date="2026-10-02") == 0,
)
# quota_ok pakai tanggal sistem -> patch _today buat simulasi ganti hari
orig_today = db._today
db._today = lambda: "2026-10-02"
try:
    check("5x di tgl A tidak ganggu jatah tgl B", quota_ok(conn, 1) is True)
finally:
    db._today = orig_today

print("== premium unlimited ==")
conn2 = fresh_db()
db.upsert_user(conn2, 2, "sultan")
conn2.execute("UPDATE users SET is_premium=1 WHERE user_id=2")
conn2.commit()
for _ in range(10):
    db.inc_downloads_today(conn2, 2, date="2026-10-01")
check("premium tetap boleh walau >5x", quota_ok(conn2, 2) is True)
check("is_premium True", db.is_premium(conn2, 2) is True)
check("user biasa is_premium False", db.is_premium(conn, 1) is False)
check("user belum terdaftar is_premium False", db.is_premium(conn, 999) is False)

print("== deteksi URL ==")
check("URL ketemu di teks", URL_RE.search("eh liat ini https://vt.tiktok.com/abc123 keren") is not None)
check("teks biasa bukan URL", URL_RE.search("halo apa kabar") is None)
check(
    "ambil URL pertama",
    URL_RE.search("a https://x.com/1 b https://y.com/2").group(0) == "https://x.com/1",
)
check("http:// juga ketemu", URL_RE.search("http://example.com/v.mp4") is not None)

print("== is_in_session (anonchat) ==")
pairing.stop(11)
pairing.stop(22)
check("belum search -> tidak dalam sesi", is_in_session(11) is False)
pairing.search(11)
check("sendiri di antrean -> belum sesi", is_in_session(11) is False)
pairing.search(22)  # pair dengan 11
check("sudah paired -> dalam sesi", is_in_session(11) is True)
check("partner juga dalam sesi", is_in_session(22) is True)
pairing.stop(11)
pairing.stop(22)
check("setelah stop -> keluar sesi", is_in_session(11) is False)

print(f"\nSEMUA {passed} TEST HIJAU")

# --- tambahan: deteksi tiktok & fallback ---
from plugins.downloader import _is_tiktok

def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    assert cond, name

_t("tiktok terdeteksi", _is_tiktok("https://vm.tiktok.com/ZSbUquTqk/"))
_t("tiktok www terdeteksi", _is_tiktok("https://www.tiktok.com/@a/video/123"))
_t("youtube bukan tiktok", not _is_tiktok("https://youtu.be/abc"))
_t("ig bukan tiktok", not _is_tiktok("https://www.instagram.com/reel/abc/"))
print("TAMBAHAN OK")

# --- wiring: client default dulu, fallback android kalau kena bot-check ---
print("== wiring extractor_args ==")
import plugins.downloader as dlmod

calls = []
fail_mode = None  # None | "botcheck" | "other"

class FakeYDL:
    def __init__(self, opts):
        calls.append(dict(opts))
        if fail_mode == "botcheck" and len(calls) == 1:
            raise RuntimeError("Sign in to confirm you're not a bot")
        if fail_mode == "other" and len(calls) == 1:
            raise RuntimeError("Video unavailable")
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def extract_info(self, url, download=True):
        return {"id": "x", "ext": "mp4", "title": "t"}
    def prepare_filename(self, info):
        return "/tmp/fake.mp4"

orig_ydl = dlmod.yt_dlp.YoutubeDL
orig_exists = __import__("os").path.exists
dlmod.yt_dlp.YoutubeDL = FakeYDL
__import__("os").path.exists = lambda p: p == "/tmp/fake.mp4"  # skip cookiefile, lolos cek hasil
try:
    # 1. normal: percobaan pertama TANPA extractor_args (client default)
    calls.clear(); fail_mode = None
    dlmod._fetch("https://youtu.be/abc")
    _t("default tanpa extractor_args", "extractor_args" not in calls[0])
    _t("format tetap best<45M", calls[0].get("format") == "best[filesize<45M]/best")
    _t("cuma 1x percobaan kalau sukses", len(calls) == 1)

    # 2. kena bot-check -> coba lagi pakai player_client android
    calls.clear(); fail_mode = "botcheck"
    dlmod._fetch("https://youtu.be/abc")
    _t("retry 2x saat bot-check", len(calls) == 2)
    ea = calls[1].get("extractor_args", {})
    _t("fallback youtube player_client=android",
       ea.get("youtube", {}).get("player_client") == ["android"])

    # 3. error lain (bukan bot-check) langsung raise, tanpa retry
    calls.clear(); fail_mode = "other"
    try:
        dlmod._fetch("https://youtu.be/abc")
        _t("error non-botcheck raise", False)
    except RuntimeError as e:
        _t("error non-botcheck raise", "Video unavailable" in str(e) and len(calls) == 1)
    print("WIRING OK")
finally:
    dlmod.yt_dlp.YoutubeDL = orig_ydl
    __import__("os").path.exists = orig_exists

# --- situs diparkir: dibalas pesan jelas, tidak di-download, tidak makan jatah ---
print("== parked ==")
import asyncio
import plugins.downloader as dlmod3
from plugins.downloader import _is_parked, _is_tiktok

_t("youtube.com diparkir", _is_parked("https://www.youtube.com/watch?v=abc"))
_t("youtu.be diparkir", _is_parked("https://youtu.be/abc"))
_t("instagram diparkir", _is_parked("https://www.instagram.com/reel/abc/"))
_t("facebook diparkir", _is_parked("https://www.facebook.com/share/r/abc/"))
_t("x diparkir", _is_parked("https://x.com/user/status/123"))
_t("tiktok TIDAK diparkir", not _is_parked("https://www.tiktok.com/@a/video/123"))
_t("tiktok kedeteksi", _is_tiktok("https://www.tiktok.com/@a/video/123"))

fetch_calls = []
class FakeYDL3:
    def __init__(self, opts):
        fetch_calls.append(opts)
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def extract_info(self, url, download=True):
        return {"id": "x", "ext": "mp4", "title": "t"}
    def prepare_filename(self, info):
        return "/tmp/fake.mp4"

class _Msg:
    def __init__(self):
        self.texts = []
        self.edited = []
    async def reply_text(self, t):
        self.texts.append(t)
        return self
    async def edit_text(self, t):
        self.edited.append(t)
    async def delete(self):
        pass

class _Upd:
    def __init__(self):
        self.message = _Msg()
        self.effective_user = type("U", (), {"id": 99, "username": "tester"})()

class _Ctx:
    bot_data = {"db": object()}

orig_upsert = dlmod3.upsert_user
orig_premium = dlmod3.is_premium
orig_quota = dlmod3.quota_ok
dlmod3.upsert_user = lambda *a: None
dlmod3.is_premium = lambda *a: False
dlmod3.quota_ok = lambda *a: True
dlmod3.yt_dlp.YoutubeDL = FakeYDL3
try:
    u = _Upd()
    asyncio.run(dlmod3._download_flow(u, _Ctx(), "https://youtu.be/abc123"))
    _t("yt-dlp tidak dipanggil", fetch_calls == [])
    _t("dibalas pesan parkir", any("diparkir" in t for t in u.message.texts))
    # link non-parkir tetap jalan normal (yt-dlp dipanggil)
    fetch_calls.clear()
    orig_exists3 = __import__("os").path.exists
    __import__("os").path.exists = lambda p: True
    u2 = _Upd()
    try:
        asyncio.run(dlmod3._download_flow(u2, _Ctx(), "https://www.tiktok.com/@a/video/123"))
    except Exception:
        pass  # kirim video butuh objek telegram asli; cukup pastikan _fetch kepanggil
    finally:
        __import__("os").path.exists = orig_exists3
    _t("tiktok tetap coba download", len(fetch_calls) == 1)
    print("PARKED OK")
finally:
    dlmod3.upsert_user = orig_upsert
    dlmod3.is_premium = orig_premium
    dlmod3.quota_ok = orig_quota
    dlmod3.yt_dlp.YoutubeDL = orig_ydl

# --- timeout: _fetch yang gantung -> user dapat pesan, bukan digantung ---
print("== timeout ==")
import time as _time
orig_fetch3 = dlmod3._fetch
orig_timeout = dlmod3.FETCH_TIMEOUT
dlmod3.upsert_user = lambda *a: None
dlmod3.is_premium = lambda *a: False
dlmod3.quota_ok = lambda *a: True
dlmod3._fetch = lambda url: _time.sleep(5)
dlmod3.FETCH_TIMEOUT = 0.2
try:
    u3 = _Upd()
    asyncio.run(dlmod3._download_flow(u3, _Ctx(), "https://www.tiktok.com/@a/video/123"))
    _t("timeout dibalas pesan", any("Kelamaan" in t for t in u3.message.edited))
    print("TIMEOUT OK")
finally:
    dlmod3._fetch = orig_fetch3
    dlmod3.FETCH_TIMEOUT = orig_timeout
    dlmod3.upsert_user = orig_upsert
    dlmod3.is_premium = orig_premium
    dlmod3.quota_ok = orig_quota
