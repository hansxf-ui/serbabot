"""Test plugin kuis: poll quiz terkirim, skor & koin benar, quota 3/hari.

Semua di-mock (Telegram). Tidak ada network sungguhan.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.kuis as kuis
from telegram.ext import CommandHandler, PollAnswerHandler

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


class FakePoll:
    def __init__(self, pid):
        self.id = pid


class FakeSentMessage:
    def __init__(self, pid):
        self.poll = FakePoll(pid)


class FakeMessage:
    def __init__(self, ctx):
        self._ctx = ctx
        self.replies = []
        self.polls = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)

    async def reply_poll(self, question, options, **kw):
        self.polls.append((question, options, kw))
        pid = "poll%d" % len(self.polls)
        return FakeSentMessage(pid)


class FakeChat:
    def __init__(self, cid=-100):
        self.id = cid


class FakePollAnswer:
    def __init__(self, poll_id, uid, option_ids):
        self.poll_id = poll_id
        self.user = FakeUser(uid)
        self.option_ids = option_ids


class FakeUpdate:
    def __init__(self, uid, poll_answer=None):
        self.effective_user = FakeUser(uid)
        self.effective_chat = FakeChat()
        self.message = FakeMessage(None)
        self.poll_answer = poll_answer


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class FakeContext:
    def __init__(self, conn, admin_id=None):
        self.bot_data = {"db": conn, "admin_id": admin_id}
        self.bot = FakeBot()
        self.args = []


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, h):
        self.handlers.append(h)


# deterministik: selalu ambil soal pertama
class FakeRandom:
    def choice(self, seq):
        return seq[0]


kuis.random = FakeRandom()
SOAL0 = kuis.SOAL[0]

print("== kuis ==")

# 1. register: tabel dibuat + 3 handler terdaftar
conn = db.init_db(":memory:")
app = FakeApp()
kuis.register(app, conn)
cmds = {c for h in app.handlers if isinstance(h, CommandHandler)
        for c in h.commands}
_t("register: /kuis & /top terdaftar", {"kuis", "top"} <= cmds)
_t("register: PollAnswerHandler terdaftar",
   any(isinstance(h, PollAnswerHandler) for h in app.handlers))
_t("register: tabel kuis_scores ada",
   conn.execute("SELECT name FROM sqlite_master WHERE name='kuis_scores'"
                ).fetchone() is not None)

# 2. /kuis: poll quiz terkirim dengan benar + jatah kepotong 1
ctx = FakeContext(conn)
upd = FakeUpdate(111)
asyncio.run(kuis.kuis_cmd(upd, ctx))
_t("poll terkirim 1x", len(upd.message.polls) == 1)
q, opts, kw = upd.message.polls[0]
_t("type=quiz", kw.get("type") == "quiz")
_t("correct_option_id sesuai soal",
   kw.get("correct_option_id") == SOAL0["answer"])
_t("is_anonymous=False", kw.get("is_anonymous") is False)
_t("soal & opsi sesuai", q == SOAL0["q"] and list(opts) == SOAL0["options"])
_t("jatah kepotong 1", db.get_quota_today(conn, "kuis", 111) == 1)
_t("poll tercatat di bot_data", "poll1" in ctx.bot_data["kuis_polls"])

# 3. quota 3/hari: ke-4 ditolak, poll tidak dikirim
for _ in range(2):
    asyncio.run(kuis.kuis_cmd(FakeUpdate(111), ctx))
upd4 = FakeUpdate(111)
asyncio.run(kuis.kuis_cmd(upd4, ctx))
_t("jatah habis: ditolak", any("habis" in r for r in upd4.message.replies))
_t("jatah habis: poll tidak dikirim", upd4.message.polls == [])
_t("jatah tidak nambah pas ditolak",
   db.get_quota_today(conn, "kuis", 111) == 3)

# 4. jawaban benar: skor +1, koin +5, ada ucapan
ctx2 = FakeContext(conn)
upd_q = FakeUpdate(222)
asyncio.run(kuis.kuis_cmd(upd_q, ctx2))
pid = "poll1"
ans_upd = FakeUpdate(222, FakePollAnswer(pid, 222, [SOAL0["answer"]]))
asyncio.run(kuis.kuis_answer(ans_upd, ctx2))
row = conn.execute(
    "SELECT score, answered FROM kuis_scores WHERE user_id=222").fetchone()
_t("jawaban benar: skor 1, answered 1", row == (1, 1))
_t("jawaban benar: +5 koin", db.get_coins(conn, 222) == 5)
_t("jawaban benar: ada ucapan di chat",
   len(ctx2.bot.sent) == 1 and ctx2.bot.sent[0][0] == -100
   and "Mantap" in ctx2.bot.sent[0][1] and "+5 koin" in ctx2.bot.sent[0][1])

# 5. jawaban salah: answered +1, skor tetap, tanpa koin/ucapan
ans_wrong = FakeUpdate(333, FakePollAnswer(pid, 333, [(SOAL0["answer"] + 1) % 4]))
asyncio.run(kuis.kuis_answer(ans_wrong, ctx2))
row = conn.execute(
    "SELECT score, answered FROM kuis_scores WHERE user_id=333").fetchone()
_t("jawaban salah: skor 0, answered 1", row == (0, 1))
_t("jawaban salah: tanpa koin", db.get_coins(conn, 333) == 0)
_t("jawaban salah: tanpa ucapan", len(ctx2.bot.sent) == 1)

# 6. jawab 2x di poll yang sama: dihitung 1x saja
ans_dup = FakeUpdate(222, FakePollAnswer(pid, 222, [SOAL0["answer"]]))
asyncio.run(kuis.kuis_answer(ans_dup, ctx2))
row = conn.execute(
    "SELECT score, answered FROM kuis_scores WHERE user_id=222").fetchone()
_t("double answer: tidak dihitung 2x", row == (1, 1))
_t("double answer: koin tidak nambah", db.get_coins(conn, 222) == 5)

# 7. poll tak dikenal: diabaikan
ans_unknown = FakeUpdate(444, FakePollAnswer("poll-ngaco", 444, [0]))
asyncio.run(kuis.kuis_answer(ans_unknown, ctx2))
_t("poll tak dikenal: diabaikan",
   conn.execute("SELECT COUNT(*) FROM kuis_scores WHERE user_id=444"
                ).fetchone()[0] == 0)

# 8. /top: leaderboard harian + mingguan
top_upd = FakeUpdate(111)
asyncio.run(kuis.top_cmd(top_upd, ctx))
txt = "\n".join(top_upd.message.replies)
_t("/top: ada judul", "Leaderboard" in txt)
_t("/top: ada section harian & mingguan",
   "Hari ini" in txt and "7 hari" in txt)
_t("/top: user 222 muncul dengan skor 1", "@tester222" in txt and "1 bener" in txt)

# 9. admin (VIP): unlimited walau jatah penuh
ctx_admin = FakeContext(conn, admin_id=777)
for _ in range(3):
    db.inc_quota_today(conn, "kuis", 777)
upd_vip = FakeUpdate(777)
asyncio.run(kuis.kuis_cmd(upd_vip, ctx_admin))
_t("admin: tetap dilayani walau jatah penuh", len(upd_vip.message.polls) == 1)

print("KUIS " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
