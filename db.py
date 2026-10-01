"""SQLite helpers — stdlib sqlite3 doang, tanpa ORM."""
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
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def init_db(path="serbabot.db"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(SCHEMA)
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


def _today():
    return time.strftime("%Y-%m-%d")


def is_premium(conn, user_id):
    row = conn.execute(
        "SELECT is_premium FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    return bool(row and row[0])


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
