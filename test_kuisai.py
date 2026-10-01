"""Test plugin kuisai: parse JSON robust, GEMINI_API_KEY wajib, quota 2/hari,
PollAnswerHandler pakai key sendiri.

Semua di-mock (ask_gemini + Telegram). Tidak ada network sungguhan.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.kuisai as kuisai
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


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.edits = []
        self.polls = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self  # jadi "status message" yang bisa di-edit

    async def edit_text(self, text, **kw):
        self.edits.append(text)

    async def reply_poll(self, question, options, **kw):
        self.polls.append((question, options, kw))
        pid = "kp%d" % len(self.polls)

        class FakeSent:
            class poll:
                id = pid
        return FakeSent()


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
        self.message = FakeMessage()
        self.poll_answer = poll_answer


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class FakeContext:
    def __init__(self, conn, args, admin_id=None):
        self.args = args
        self.bot_data = {"db": conn, "admin_id": admin_id}
        self.bot = FakeBot()


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, h):
        self.handlers.append(h)


# ---------- fake ask_gemini ----------

def _soal(i):
    return {"q": "Soal %d?" % i,
            "options": ["a%d" % i, "b%d" % i, "c%d" % i, "d%d" % i],
            "answer": i % 4}


GOOD_JSON = json.dumps([_soal(i) for i in range(5)])
FENCED = "```json\n" + GOOD_JSON + "\n```"
FENCED_PLAIN = "```\n" + GOOD_JSON + "\n```"
WITH_PROSE = "Nih 5 soalnya:\n" + GOOD_JSON + "\nSemoga beruntung!"

gemini_calls = {}


def fake_ask_gemini(system_prompt, question, api_key, max_tokens=1000):
    gemini_calls["system"] = system_prompt
    gemini_calls["max_tokens"] = max_tokens
    return gemini_calls.get("response", GOOD_JSON)


kuisai.ask_gemini, saved_ask = fake_ask_gemini, kuisai.ask_gemini
saved_key = os.environ.get("GEMINI_API_KEY")
os.environ["GEMINI_API_KEY"] = "TESTKEY"

print("== kuisai ==")

# 1. register: handler terdaftar
conn = db.init_db(":memory:")
app = FakeApp()
kuisai.register(app, conn)
cmds = {c for h in app.handlers if isinstance(h, CommandHandler)
        for c in h.commands}
_t("register: /kuisai terdaftar", "kuisai" in cmds)
_t("register: PollAnswerHandler terdaftar",
   any(isinstance(h, PollAnswerHandler) for h in app.handlers))

# 2. parse JSON robust
_t("parse: JSON polos", len(kuisai._parse_quiz(GOOD_JSON)) == 5)
_t("parse: fence ```json", len(kuisai._parse_quiz(FENCED)) == 5)
_t("parse: fence ``` biasa", len(kuisai._parse_quiz(FENCED_PLAIN)) == 5)
_t("parse: ada teks pembuka/penutup", len(kuisai._parse_quiz(WITH_PROSE)) == 5)
q0 = kuisai._parse_quiz(GOOD_JSON)[0]
_t("parse: isi soal benar",
   q0["q"] == "Soal 0?" and q0["options"] == ["a0", "b0", "c0", "d0"]
   and q0["answer"] == 0)
for bad in ["bukan json", "[1,2", "[]", '{"q": "x"}',
            json.dumps([{"q": "x", "options": ["a", "b"], "answer": 5}])]:
    try:
        kuisai._parse_quiz(bad)
        ok = False
    except (ValueError, KeyError, TypeError):
        ok = True
    _t("parse gagal ramah: %r" % bad[:20], ok)

# 3. /kuisai sukses: 5 poll quiz + system prompt sesuai spec
ctx = FakeContext(conn, ["sejarah", "Indonesia"])
upd = FakeUpdate(111)
asyncio.run(kuisai.kuisai_cmd(upd, ctx))
_t("5 poll terkirim", len(upd.message.polls) == 5)
qs = [p[0] for p in upd.message.polls]
_t("soal dari AI dipakai", qs == ["Soal %d?" % i for i in range(5)])
_t("type=quiz semua", all(p[2].get("type") == "quiz" for p in upd.message.polls))
_t("correct_option_id sesuai", all(
    p[2].get("correct_option_id") == i % 4 for i, p in enumerate(upd.message.polls)))
_t("is_anonymous=False", all(p[2].get("is_anonymous") is False
                             for p in upd.message.polls))
_t("system prompt: topik masuk",
   "tentang: sejarah Indonesia" in gemini_calls["system"])
_t("system prompt: minta JSON array",
   '[{"q": "...", "options": ["a","b","c","d"], "answer": 0}]'
   in gemini_calls["system"])
_t("max_tokens 1500", gemini_calls["max_tokens"] == 1500)
_t("poll tercatat di key kuisai_polls (bukan kuis_polls)",
   len(ctx.bot_data.get("kuisai_polls", {})) == 5
   and "kuis_polls" not in ctx.bot_data)
_t("jatah kepotong 1 SETELAH poll pertama", db.get_quota_today(conn, "kuisai", 111) == 1)

# 4. quota 2/hari: ke-3 ditolak
asyncio.run(kuisai.kuisai_cmd(FakeUpdate(111), FakeContext(conn, ["x"])))
upd4 = FakeUpdate(111)
ctx4 = FakeContext(conn, ["x"])
asyncio.run(kuisai.kuisai_cmd(upd4, ctx4))
_t("jatah habis: ditolak", any("2/hari" in r for r in upd4.message.replies))
_t("jatah habis: poll tidak dikirim", upd4.message.polls == [])
_t("jatah tidak nambah pas ditolak",
   db.get_quota_today(conn, "kuisai", 111) == 2)

# 5. format AI aneh -> pesan ramah, jatah tidak kepotong
gemini_calls["response"] = "maaf saya tidak bisa"
upd5 = FakeUpdate(222)
asyncio.run(kuisai.kuisai_cmd(upd5, FakeContext(conn, ["x"])))
_t("format aneh: pesan ramah",
   any("format aneh" in e for e in upd5.message.edits))
_t("format aneh: jatah tidak kepotong",
   db.get_quota_today(conn, "kuisai", 222) == 0)
gemini_calls["response"] = GOOD_JSON

# 6. AI error -> pesan ramah, jatah tidak kepotong
def _boom(*a):
    raise RuntimeError("gemini down")

kuisai.ask_gemini = _boom
upd6 = FakeUpdate(333)
asyncio.run(kuisai.kuisai_cmd(upd6, FakeContext(conn, ["x"])))
_t("AI error: pesan ramah", any("error" in e for e in upd6.message.edits))
_t("AI error: jatah tidak kepotong",
   db.get_quota_today(conn, "kuisai", 333) == 0)
kuisai.ask_gemini = fake_ask_gemini

# 7. jawaban benar: +5 koin "kuis AI benar" + ucapan
ctx7 = FakeContext(conn, ["matematika"])
upd_q = FakeUpdate(444)
asyncio.run(kuisai.kuisai_cmd(upd_q, ctx7))
ans_upd = FakeUpdate(444, FakePollAnswer("kp1", 444, [0]))  # soal 0 answer=0
asyncio.run(kuisai.kuisai_answer(ans_upd, ctx7))
_t("jawaban benar: +5 koin", db.get_coins(conn, 444) == 5)
tx = db.get_coin_history(conn, 444)
_t("jawaban benar: reason 'kuis AI benar'",
   tx and tx[0][1] == "kuis AI benar")
_t("jawaban benar: ada ucapan",
   len(ctx7.bot.sent) == 1 and "Mantap" in ctx7.bot.sent[0][1]
   and "+5 koin" in ctx7.bot.sent[0][1])

# 8. jawaban salah: tanpa koin/ucapan; double answer dihitung 1x
ans_wrong = FakeUpdate(555, FakePollAnswer("kp2", 555, [0]))  # soal 1 answer=1
asyncio.run(kuisai.kuisai_answer(ans_wrong, ctx7))
_t("jawaban salah: tanpa koin", db.get_coins(conn, 555) == 0)
_t("jawaban salah: tanpa ucapan", len(ctx7.bot.sent) == 1)
ans_dup = FakeUpdate(444, FakePollAnswer("kp1", 444, [0]))
asyncio.run(kuisai.kuisai_answer(ans_dup, ctx7))
_t("double answer: koin tidak nambah", db.get_coins(conn, 444) == 5)

# 9. PollAnswerHandler pakai mapping sendiri: poll milik plugin kuis diabaikan
ctx9 = FakeContext(conn, ["x"])
ctx9.bot_data["kuis_polls"] = {"poll-kuis": {"user_id": 666, "chat_id": -100,
                                             "correct": 2}}
ans_other = FakeUpdate(666, FakePollAnswer("poll-kuis", 666, [2]))
asyncio.run(kuisai.kuisai_answer(ans_other, ctx9))
_t("poll plugin kuis: diabaikan kuisai", db.get_coins(conn, 666) == 0)
ans_unknown = FakeUpdate(777, FakePollAnswer("poll-ngaco", 777, [0]))
asyncio.run(kuisai.kuisai_answer(ans_unknown, ctx9))
_t("poll tak dikenal: diabaikan", db.get_coins(conn, 777) == 0)

# 10. tanpa topik -> cara pakai; env kosong -> pesan jujur
upd10 = FakeUpdate(888)
asyncio.run(kuisai.kuisai_cmd(upd10, FakeContext(conn, [])))
_t("tanpa topik: kasih cara pakai",
   any("/kuisai <topik>" in r for r in upd10.message.replies))
del os.environ["GEMINI_API_KEY"]
upd11 = FakeUpdate(999)
asyncio.run(kuisai.kuisai_cmd(upd11, FakeContext(conn, ["x"])))
_t("env kosong: pesan jujur minta admin setting",
   any("GEMINI_API_KEY" in r for r in upd11.message.replies))
_t("env kosong: jatah tidak kepotong",
   db.get_quota_today(conn, "kuisai", 999) == 0)

# 11. admin (VIP): unlimited walau jatah penuh
os.environ["GEMINI_API_KEY"] = "TESTKEY"
ctx_admin = FakeContext(conn, ["x"], admin_id=1234)
for _ in range(2):
    db.inc_quota_today(conn, "kuisai", 1234)
upd_vip = FakeUpdate(1234)
asyncio.run(kuisai.kuisai_cmd(upd_vip, ctx_admin))
_t("admin: tetap dilayani walau jatah penuh", len(upd_vip.message.polls) == 5)

kuisai.ask_gemini = saved_ask
if saved_key is not None:
    os.environ["GEMINI_API_KEY"] = saved_key
elif "GEMINI_API_KEY" in os.environ:
    del os.environ["GEMINI_API_KEY"]

print("KUISAI " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
