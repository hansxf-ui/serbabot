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
