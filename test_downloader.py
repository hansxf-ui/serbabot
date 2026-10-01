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
