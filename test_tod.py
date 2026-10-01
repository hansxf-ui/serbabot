"""Test plugin tod: menu tombol, kartu truth/dare, quota 10/hari.

Semua di-mock (Telegram). Tidak ada network sungguhan.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.tod as tod
from telegram.ext import CallbackQueryHandler, CommandHandler

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester%d" % uid


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw.get("reply_markup")))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answered = False
        self.edits = []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw.get("reply_markup")))


class FakeChat:
    def __init__(self, cid=-200):
        self.id = cid


class FakeUpdate:
    def __init__(self, uid, query=None):
        self.effective_user = FakeUser(uid)
        self.effective_chat = FakeChat()
        self.message = FakeMessage()
        self.callback_query = query


class FakeContext:
    def __init__(self, conn, admin_id=None):
        self.bot_data = {"db": conn, "admin_id": admin_id}
        self.args = []


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, h):
        self.handlers.append(h)


# deterministik: selalu ambil kartu pertama
class FakeRandom:
    def choice(self, seq):
        return seq[0]


tod.random = FakeRandom()
TRUTH0 = [c for c in tod.DECK if c["type"] == "truth"][0]
DARE0 = [c for c in tod.DECK if c["type"] == "dare"][0]

print("== tod ==")

# 1. register: handler terdaftar, deck valid
conn = db.init_db(":memory:")
app = FakeApp()
tod.register(app, conn)
cmds = {c for h in app.handlers if isinstance(h, CommandHandler)
        for c in h.commands}
_t("register: /tod terdaftar", "tod" in cmds)
cb = [h for h in app.handlers if isinstance(h, CallbackQueryHandler)]
_t("register: CallbackQueryHandler pattern ^tod:",
   len(cb) == 1 and cb[0].pattern.pattern == r"^tod:")
_t("deck: 120 kartu", len(tod.DECK) == 120)

# 2. /tod: menu dengan 2 tombol
ctx = FakeContext(conn)
upd = FakeUpdate(111)
asyncio.run(tod.tod_cmd(upd, ctx))
_t("/tod: 1 pesan menu", len(upd.message.replies) == 1)
text, markup = upd.message.replies[0]
btns = [b.callback_data for row in markup.inline_keyboard for b in row]
_t("/tod: tombol truth & dare", btns == ["tod:truth", "tod:dare"])
_t("/tod: buka menu tidak makan jatah",
   db.get_quota_today(conn, "tod", 111) == 0)

# 3. pilih truth: kartu truth terkirim + jatah kepotong 1
q = FakeQuery("tod:truth")
upd_t = FakeUpdate(111, q)
asyncio.run(tod.tod_cb(upd_t, ctx))
_t("truth: callback di-answer", q.answered)
_t("truth: kartu terkirim", len(q.edits) == 1)
etext, emarkup = q.edits[0]
_t("truth: judul + isi kartu",
   etext.startswith("TRUTH 🎲") and TRUTH0["text"] in etext)
_t("truth: tombol menu tetap ada",
   [b.callback_data for row in emarkup.inline_keyboard for b in row]
   == ["tod:truth", "tod:dare"])
_t("truth: jatah kepotong 1", db.get_quota_today(conn, "tod", 111) == 1)

# 4. pilih dare: kartu dare terkirim
q2 = FakeQuery("tod:dare")
asyncio.run(tod.tod_cb(FakeUpdate(111, q2), ctx))
_t("dare: kartu dare terkirim",
   len(q2.edits) == 1 and q2.edits[0][0].startswith("DARE 🔥")
   and DARE0["text"] in q2.edits[0][0])
_t("dare: jatah kepotong lagi", db.get_quota_today(conn, "tod", 111) == 2)

# 5. quota 10/hari: ke-11 ditolak
for _ in range(8):
    db.inc_quota_today(conn, "tod", 111)
q3 = FakeQuery("tod:truth")
asyncio.run(tod.tod_cb(FakeUpdate(111, q3), ctx))
_t("jatah habis: ditolak",
   len(q3.edits) == 1 and "habis" in q3.edits[0][0])
_t("jatah habis: kartu tidak dikirim", TRUTH0["text"] not in q3.edits[0][0])
_t("jatah tidak nambah pas ditolak",
   db.get_quota_today(conn, "tod", 111) == 10)

# 6. admin (VIP): unlimited
ctx_admin = FakeContext(conn, admin_id=777)
for _ in range(10):
    db.inc_quota_today(conn, "tod", 777)
q4 = FakeQuery("tod:dare")
asyncio.run(tod.tod_cb(FakeUpdate(777, q4), ctx_admin))
_t("admin: tetap dilayani walau jatah penuh",
   len(q4.edits) == 1 and DARE0["text"] in q4.edits[0][0])

print("TOD " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
