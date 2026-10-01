"""Test fondasi koin: check-in/streak, saldo, quota generik, premium_until.

Tanpa network. Simulasi tanggal via parameter date=.
"""
import asyncio
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.koin as koin

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


def fresh():
    conn = db.init_db(":memory:")
    db.upsert_user(conn, 1, "user1")
    db.upsert_user(conn, 9, "admin")
    return conn


# ---------- check-in & streak ----------
conn = fresh()
r = db.checkin(conn, 1, date="2026-10-01")
_t("checkin hari 1: streak=1", r == {"already": False, "streak": 1, "bonus": 10})
_t("saldo 10", db.get_coins(conn, 1) == 10)

r = db.checkin(conn, 1, date="2026-10-01")
_t("checkin 2x sehari ditolak", r["already"] and r["streak"] == 1)
_t("saldo tetap 10", db.get_coins(conn, 1) == 10)

r = db.checkin(conn, 1, date="2026-10-02")
_t("hari 2: streak=2 bonus 10", r["streak"] == 2 and r["bonus"] == 10)

r = db.checkin(conn, 1, date="2026-10-04")  # bolong 1 hari
_t("bolong: streak reset ke 1", r["streak"] == 1)

# tangga bonus: bangun streak 1..7
conn = fresh()
for i in range(1, 8):
    d = (datetime.date(2026, 10, 1) + datetime.timedelta(days=i - 1)).isoformat()
    r = db.checkin(conn, 1, date=d)
_t("hari 7: bonus 60 (10+50)", r["streak"] == 7 and r["bonus"] == 60)
_t("get_streak konsisten", db.get_streak(conn, 1) == (7, "2026-10-07"))

# ---------- koin ----------
conn = fresh()
db.add_coins(conn, 1, 100, "tes")
_t("add_coins 100", db.get_coins(conn, 1) == 100)
_t("spend cukup -> True", db.spend_coins(conn, 1, 40, "beli") is True)
_t("saldo 60", db.get_coins(conn, 1) == 60)
_t("spend kurang -> False", db.spend_coins(conn, 1, 999, "mahal") is False)
_t("saldo tetap 60", db.get_coins(conn, 1) == 60)
hist = db.get_coin_history(conn, 1)
_t("riwayat 2 entri, terbaru dulu", len(hist) == 2 and hist[0][0] == -40)
try:
    db.add_coins(conn, 1, 0, "nol")
    _t("add_coins 0 ditolak", False)
except ValueError:
    _t("add_coins 0 ditolak", True)

# ---------- quota generik ----------
conn = fresh()
_t("quota awal 0", db.get_quota_today(conn, "kuis", 1) == 0)
db.inc_quota_today(conn, "kuis", 1)
db.inc_quota_today(conn, "kuis", 1)
_t("quota 2", db.get_quota_today(conn, "kuis", 1) == 2)
_t("fitur lain terpisah", db.get_quota_today(conn, "tod", 1) == 0)
_t("sisa = limit - pakai", db.quota_remaining(conn, "kuis", 1, 5, 9) == 3)
_t("vip = unlimited", db.quota_remaining(conn, "kuis", 9, 5, 9) == float("inf"))
_t("habis = 0", db.quota_remaining(conn, "kuis", 1, 2, 9) == 0)

# ---------- premium_until (migrasi aditif) ----------
# DB "lama": tabel users tanpa kolom premium_until
conn = sqlite3.connect(":memory:", check_same_thread=False)
conn.execute(
    "CREATE TABLE users(user_id INTEGER PRIMARY KEY, username TEXT, "
    "is_premium INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"
)
conn.execute("INSERT INTO users VALUES(1,'lama',0,'2026-01-01T00:00:00')")
conn.commit()
db._migrate(conn)
cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
_t("migrasi tambah kolom premium_until", "premium_until" in cols)
_t("data lama tidak hilang", conn.execute("SELECT username FROM users").fetchone()[0] == "lama")
_t("migrasi idempoten", (db._migrate(conn), True)[1])

conn = fresh()
db.grant_premium_days(conn, 1, 30)
_t("grant 30 hari -> premium", db.is_premium(conn, 1))
until = db.premium_until(conn, 1)
_t("premium_until format ISO", until and "T" in until)
# tumpuk: grant lagi dari sisa
db.grant_premium_days(conn, 1, 30)
until2 = db.premium_until(conn, 1)
_t("grant kedua menumpuk", until2 > until)
# kedaluwarsa
conn.execute("UPDATE users SET premium_until='2020-01-01T00:00:00' WHERE user_id=1")
_t("premium kedaluwarsa -> bukan premium", not db.is_premium(conn, 1))
# is_premium lama (kolom) tetap jalan
conn.execute("UPDATE users SET is_premium=1, premium_until=NULL WHERE user_id=1")
_t("is_premium=1 tetap vip", db.is_premium(conn, 1) and db.is_vip(conn, 1, 9))

# ---------- handler /checkin & /koin (mock Telegram) ----------
class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester"


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn):
        self.args = []
        self.bot_data = {"db": conn}


conn = fresh()


async def _run():
    u = FakeUpdate(7)
    c = FakeContext(conn)
    await koin.checkin_cmd(u, c)
    r1 = u.message.replies[-1]
    u2 = FakeUpdate(7)
    await koin.koin_cmd(u2, c)
    r2 = u2.message.replies[-1]
    return r1, r2


r1, r2 = asyncio.run(_run())
_t("/checkin balas streak & koin", "hari ke-1" in r1 and "+10 koin" in r1)
_t("/koin balas saldo & riwayat", "Koin Serba: 10" in r2 and "check-in hari ke-1" in r2)

print(f"\n{('KOIN OK' if not fails else 'KOIN GAGAL')} ({fails and len(fails)} gagal)")
raise SystemExit(1 if fails else 0)
