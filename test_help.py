"""Test /start & /help (anonchat): WELCOME + daftar fitur lengkap."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.anonchat as anonchat

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester"


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.kwargs = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        self.kwargs.append(kw)
        return self


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn):
        self.args = []
        self.bot_data = {"db": conn, "admin_id": 9}


conn = db.init_db(":memory:")


async def _run():
    u = FakeUpdate(7)
    c = FakeContext(conn)
    await anonchat.start(u, c)
    r1 = u.message.replies[-1]
    u2 = FakeUpdate(7)
    await anonchat.help_cmd(u2, c)
    r2 = u2.message.replies[-1]
    return r1, r2


r1, r2 = asyncio.run(_run())
_t("/start sebut /help", "/help" in r1)
for cmd in ["/checkin", "/koin", "/kuis", "/kuisai", "/tod", "/menfess",
            "/invite", "/persona", "/rangkum", "/vn", "/zodiak", "/weton",
            "/jodoh", "/sholat", "/skor", "/gacha", "/koleksi", "/meme",
            "/stiker", "/memein", "/resi", "/katalog", "/premium",
            "/ai", "/gambar", "/top", "/search"]:
    _t(f"/help daftar {cmd}", cmd in r2)

print(f"\n{('HELP OK' if not fails else 'HELP GAGAL')} ({len(fails)} gagal)")
raise SystemExit(1 if fails else 0)
