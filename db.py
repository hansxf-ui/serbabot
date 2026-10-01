"""SQLite helpers — stdlib sqlite3 doang, tanpa ORM."""
import datetime
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    is_premium INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    reported_id INTEGER NOT NULL,
    message_text TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS downloads (
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE IF NOT EXISTS coins (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coin_tx (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkins (
    user_id INTEGER PRIMARY KEY,
    last_date TEXT,
    streak INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS quotas (
    user_id INTEGER NOT NULL,
    feature TEXT NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, feature, date)
);
"""

# Check-in: bonus dasar + tangga streak
CHECKIN_BASE = 10
CHECKIN_LADDER = {7: 50, 14: 120, 30: 300}
CHECKIN_LADDER_BIG = 300  # streak > 30: bonus tetap 300/hari


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _now_dt():
    return datetime.datetime.now().replace(microsecond=0)


def _today():
    return time.strftime("%Y-%m-%d")


def _migrate(conn):
    """Migrasi aditif untuk DB live: tambah kolom yang belum ada.

    DB di VPS berisi data user asli — JANGAN pernah DROP/ALTER yang
    merusak. Semua migrasi di sini = ADD COLUMN saja.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "premium_until" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN premium_until TEXT")
        conn.commit()


def init_db(path="serbabot.db"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def upsert_user(conn, user_id, username):
    conn.execute(
        "INSERT INTO users(user_id, username, created_at) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
        (user_id, username, _now()),
    )
    conn.commit()


def add_report(conn, reporter_id, reported_id, message_text):
    conn.execute(
        "INSERT INTO reports(reporter_id, reported_id, message_text, created_at)"
        " VALUES(?,?,?,?)",
        (reporter_id, reported_id, message_text, _now()),
    )
    conn.commit()


def is_premium(conn, user_id):
    row = conn.execute(
        "SELECT is_premium, premium_until FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row:
        return False
    if row[0]:
        return True
    until = row[1]
    return bool(until and until > _now())


def grant_premium_days(conn, user_id, days):
    """Tambah N hari premium. Kalau masih ada sisa aktif, ditumpuk dari
    sisa itu (bukan dari sekarang). User harus sudah upsert_user dulu."""
    row = conn.execute(
        "SELECT premium_until FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    base = _now_dt()
    if row and row[0]:
        try:
            cur = datetime.datetime.fromisoformat(row[0])
            if cur > base:
                base = cur
        except ValueError:
            pass
    new_until = (base + datetime.timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE users SET premium_until=? WHERE user_id=?", (new_until, user_id)
    )
    conn.commit()
    return new_until


def premium_until(conn, user_id):
    row = conn.execute(
        "SELECT premium_until FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    return row[0] if row else None


# ---------- Koin Serba ----------

def get_coins(conn, user_id):
    row = conn.execute(
        "SELECT balance FROM coins WHERE user_id=?", (user_id,)
    ).fetchone()
    return row[0] if row else 0


def _touch_coins(conn, user_id):
    conn.execute(
        "INSERT OR IGNORE INTO coins(user_id, balance, updated_at) VALUES(?,?,?)",
        (user_id, 0, _now()),
    )


def add_coins(conn, user_id, amount, reason):
    """Tambah koin (amount > 0). Return saldo baru."""
    if amount <= 0:
        raise ValueError("amount harus positif")
    _touch_coins(conn, user_id)
    conn.execute(
        "UPDATE coins SET balance=balance+?, updated_at=? WHERE user_id=?",
        (amount, _now(), user_id),
    )
    conn.execute(
        "INSERT INTO coin_tx(user_id, delta, reason, created_at) VALUES(?,?,?,?)",
        (user_id, amount, reason, _now()),
    )
    conn.commit()
    return get_coins(conn, user_id)


def spend_coins(conn, user_id, amount, reason):
    """Pakai koin. Return True kalau saldo cukup, False kalau kurang
    (saldo tidak berubah)."""
    if amount <= 0:
        raise ValueError("amount harus positif")
    if get_coins(conn, user_id) < amount:
        return False
    _touch_coins(conn, user_id)
    conn.execute(
        "UPDATE coins SET balance=balance-?, updated_at=? WHERE user_id=?",
        (amount, _now(), user_id),
    )
    conn.execute(
        "INSERT INTO coin_tx(user_id, delta, reason, created_at) VALUES(?,?,?,?)",
        (user_id, -amount, reason, _now()),
    )
    conn.commit()
    return True


def get_coin_history(conn, user_id, limit=5):
    return conn.execute(
        "SELECT delta, reason, created_at FROM coin_tx WHERE user_id=? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()


# ---------- Check-in & streak ----------

def get_streak(conn, user_id):
    row = conn.execute(
        "SELECT streak, last_date FROM checkins WHERE user_id=?", (user_id,)
    ).fetchone()
    return (row[0], row[1]) if row else (0, None)


def checkin(conn, user_id, date=None):
    """Check-in harian. Return dict(already, streak, bonus).

    Streak putus kalau ada hari yang bolong. Bonus = 10 + tangga
    (hari 7: +50, 14: +120, 30: +300, >30: +300/hari). Koin langsung
    masuk via add_coins.
    """
    date = date or _today()
    streak, last = get_streak(conn, user_id)
    if last == date:
        return {"already": True, "streak": streak, "bonus": 0}
    yesterday = (
        datetime.date.fromisoformat(date) - datetime.timedelta(days=1)
    ).isoformat()
    streak = streak + 1 if last == yesterday else 1
    bonus = CHECKIN_BASE + CHECKIN_LADDER.get(
        streak, CHECKIN_LADDER_BIG if streak > 30 else 0
    )
    conn.execute(
        "INSERT INTO checkins(user_id, last_date, streak) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_date=excluded.last_date, "
        "streak=excluded.streak",
        (user_id, date, streak),
    )
    add_coins(conn, user_id, bonus, f"check-in hari ke-{streak}")
    return {"already": False, "streak": streak, "bonus": bonus}


# ---------- Jatah harian generik ----------

def get_quota_today(conn, feature, user_id, date=None):
    """Pemakaian fitur hari ini. feature = nama plugin (mis. 'kuis')."""
    date = date or _today()
    row = conn.execute(
        "SELECT count FROM quotas WHERE user_id=? AND feature=? AND date=?",
        (user_id, feature, date),
    ).fetchone()
    return row[0] if row else 0


def inc_quota_today(conn, feature, user_id, date=None):
    date = date or _today()
    conn.execute(
        "INSERT INTO quotas(user_id, feature, date, count) VALUES(?,?,?,1) "
        "ON CONFLICT(user_id, feature, date) DO UPDATE SET count=count+1",
        (user_id, feature, date),
    )
    conn.commit()


def quota_remaining(conn, feature, user_id, limit, admin_id):
    """Sisa jatah fitur hari ini. VIP (admin/premium) = tak terbatas."""
    if is_vip(conn, user_id, admin_id):
        return float("inf")
    return max(0, limit - get_quota_today(conn, feature, user_id))


def is_vip(conn, user_id, admin_id):
    """Admin & user premium bebas jatah harian. admin_id boleh None."""
    return bool(admin_id and user_id == admin_id) or is_premium(conn, user_id)


def get_downloads_today(conn, user_id, date=None):
    date = date or _today()
    row = conn.execute(
        "SELECT count FROM downloads WHERE user_id=? AND date=?", (user_id, date)
    ).fetchone()
    return row[0] if row else 0


def inc_downloads_today(conn, user_id, date=None):
    date = date or _today()
    conn.execute(
        "INSERT INTO downloads(user_id, date, count) VALUES(?,?,1) "
        "ON CONFLICT(user_id, date) DO UPDATE SET count=count+1",
        (user_id, date),
    )
    conn.commit()
