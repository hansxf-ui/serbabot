"""Test persona AI (/persona + memori) di plugin aichat.

Semua di-mock (Telegram + AI). Tidak ada network sungguhan.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.aichat as aichat
from telegram.ext import CallbackQueryHandler, CommandHandler

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeUser:
    def __init__(self, uid, first_name="Tester"):
        self.id = uid
        self.username = "tester%d" % uid
        self.first_name = first_name


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.markups = []
        self.edits = []
        self.polls = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        self.markups.append(kw.get("reply_markup"))
        return self

    async def edit_text(self, text, **kw):
        self.edits.append(text)

    async def reply_poll(self, question, options, **kw):
        self.polls.append((question, options, kw))
        pid = "poll%d" % len(self.polls)

        class FakeSent:
            class poll:
                id = pid
        return FakeSent()


class FakeCallbackQuery:
    def __init__(self, data, uid):
        self.data = data
        self.from_user = FakeUser(uid)
        self.answered = False
        self.edited = []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kw):
        self.edited.append(text)


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()
        self.callback_query = None


class FakeCbUpdate:
    def __init__(self, uid, data):
        self.effective_user = FakeUser(uid)
        self.message = None
        self.callback_query = FakeCallbackQuery(data, uid)


class FakeContext:
    def __init__(self, conn, args):
        self.args = args
        self.bot_data = {"db": conn}


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, h):
        self.handlers.append(h)


# GEMINI_API_KEY harus kosong biar jalur persona pakai _ask_pollinations
saved_key = os.environ.pop("GEMINI_API_KEY", None)

print("== persona ==")

# 1. register: tabel dibuat + handler terdaftar
conn = db.init_db(":memory:")
app = FakeApp()
aichat.register(app, conn)
cmds = {c for h in app.handlers if isinstance(h, CommandHandler)
        for c in h.commands}
_t("register: /persona terdaftar", "persona" in cmds)
cb = [h for h in app.handlers if isinstance(h, CallbackQueryHandler)]
_t("register: CallbackQueryHandler ^persona: terdaftar",
   len(cb) == 1 and getattr(cb[0].pattern, "pattern", cb[0].pattern) == "^persona:")
_t("register: tabel persona_state ada",
   conn.execute("SELECT name FROM sqlite_master WHERE name='persona_state'"
                ).fetchone() is not None)
_t("register: tabel persona_memory ada",
   conn.execute("SELECT name FROM sqlite_master WHERE name='persona_memory'"
                ).fetchone() is not None)

# 2. /persona tanpa argumen & tanpa aktif: keyboard 4 pilihan
ctx = FakeContext(conn, [])
upd = FakeUpdate(111)
asyncio.run(aichat.persona_cmd(upd, ctx))
_t("/persona: 1x reply", len(upd.message.replies) == 1)
mk = upd.message.markups[0]
btns = [b for row in mk.inline_keyboard for b in row]
_t("/persona: 4 tombol", len(btns) == 4)
_t("/persona: callback_data persona:<key>",
   {b.callback_data for b in btns}
   == {"persona:curhat", "persona:tutor", "persona:roleplay",
       "persona:primbon"})
_t("/persona: belum aktif disebut", "Belum ada yang aktif" in upd.message.replies[0])

# 3. pilih via callback: tersimpan di DB
upd_cb = FakeCbUpdate(111, "persona:tutor")
asyncio.run(aichat.persona_cb(upd_cb, ctx))
_t("callback: dijawab", upd_cb.callback_query.answered)
_t("callback: persona tersimpan", aichat.get_persona(conn, 111) == "tutor")
_t("callback: konfirmasi nyebut Tutor Santai",
   any("Tutor Santai" in e for e in upd_cb.callback_query.edited))

# 4. /persona lagi: nyebut yang aktif
upd2 = FakeUpdate(111)
asyncio.run(aichat.persona_cmd(upd2, ctx))
_t("/persona: nyebut persona aktif",
   "Tutor Santai" in upd2.message.replies[0])

# 5. /persona <argumen>: set langsung tanpa tombol
ctx5 = FakeContext(conn, ["primbon"])
upd5 = FakeUpdate(222)
asyncio.run(aichat.persona_cmd(upd5, ctx5))
_t("/persona primbon: set langsung", aichat.get_persona(conn, 222) == "primbon")

# 6. memori: nama terekstrak dari pesan
captured = {}


def fake_poll(question, system_prompt=aichat.SYSTEM_PROMPT):
    captured["q"] = question
    captured["sys"] = system_prompt
    return "jawaban persona: halo!"


aichat._ask_pollinations, saved_poll = fake_poll, aichat._ask_pollinations
upd6 = FakeUpdate(111)  # user 111 persona=tutor
asyncio.run(aichat.ai_cmd(upd6, FakeContext(conn, ["namaku", "Budi,",
                                                  "gue", "mau", "curhat"])))
row = conn.execute(
    "SELECT name FROM persona_memory WHERE user_id=111").fetchone()
_t("memori: nama Budi terekstrak", row and row[0] == "Budi")
_t("memori: system prompt dapat konteks nama",
   "User bernama Budi." in captured.get("sys", ""))
_t("memori: system prompt pakai prompt tutor",
   "Tutor Santai" in captured.get("sys", ""))
_t("memori: jawaban diteruskan",
   upd6.message.edits == ["jawaban persona: halo!"])

# 7. memori: fakta terekstrak (80 char pertama)
upd7 = FakeUpdate(111)
asyncio.run(aichat.ai_cmd(upd7, FakeContext(conn, ["hobi", "gue", "mancing",
                                                  "di", "danau"])))
row = conn.execute(
    "SELECT name, facts FROM persona_memory WHERE user_id=111").fetchone()
_t("memori: fakta tersimpan", row and "hobi gue mancing di danau" in row[1])
_t("memori: nama lama tidak ketimpa fakta", row and row[0] == "Budi")
_t("memori: fakta ikut ke system prompt",
   "Fakta: hobi gue mancing di danau" in captured.get("sys", ""))

# 8. quota persona 20/hari (feature "persona", bukan downloads)
_t("quota persona: kepotong 2x", db.get_quota_today(conn, "persona", 111) == 2)
_t("quota persona: downloads tidak kepotong",
   db.get_downloads_today(conn, 111) == 0)
for _ in range(20):
    db.inc_quota_today(conn, "persona", 333)
aichat.set_persona(conn, 333, "curhat")
upd8 = FakeUpdate(333)
asyncio.run(aichat.ai_cmd(upd8, FakeContext(conn, ["halo"])))
_t("quota persona habis: ditolak",
   any("20/hari" in r for r in upd8.message.replies))
_t("quota persona habis: AI tidak dipanggil",
   upd8.message.edits == [])

# 9. tanpa persona: perilaku lama (downloads 5/hari)
upd9 = FakeUpdate(444)
asyncio.run(aichat.ai_cmd(upd9, FakeContext(conn, ["halo"])))
_t("tanpa persona: downloads kepotong 1",
   db.get_downloads_today(conn, 444) == 1)
_t("tanpa persona: quota persona tidak kepotong",
   db.get_quota_today(conn, "persona", 444) == 0)
_t("tanpa persona: system prompt default",
   "Kamu asisten santai" in captured.get("sys", ""))

# 10. VIP: persona unlimited
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(555,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
aichat.set_persona(conn, 555, "roleplay")
for _ in range(20):
    db.inc_quota_today(conn, "persona", 555)
upd10 = FakeUpdate(555)
asyncio.run(aichat.ai_cmd(upd10, FakeContext(conn, ["halo"])))
_t("VIP persona: tetap dilayani walau jatah penuh",
   upd10.message.edits == ["jawaban persona: halo!"])

aichat._ask_pollinations = saved_poll
if saved_key is not None:
    os.environ["GEMINI_API_KEY"] = saved_key

print("PERSONA " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
