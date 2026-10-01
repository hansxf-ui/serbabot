"""Test plugin grup: welcome, antispam, statistik, /help di private.

Semua di-mock (Telegram). Tidak ada network sungguhan.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.grup as grup

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeMember:
    def __init__(self, status):
        self.status = status


class FakeBot:
    def __init__(self, member_status="member"):
        self.deleted = []
        self.member_status = member_status  # atau Exception

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def get_chat_member(self, chat_id, user_id):
        if isinstance(self.member_status, Exception):
            raise self.member_status
        return FakeMember(self.member_status)


class FakeNewMember:
    def __init__(self, first_name=None, username=None):
        self.first_name = first_name
        self.username = username


class FakeChat:
    def __init__(self, cid, ctype="supergroup", title="Grup Test"):
        self.id = cid
        self.type = ctype
        self.title = title


class FakeUser:
    def __init__(self, uid, username="tester"):
        self.id = uid
        self.username = username


class FakeMessage:
    def __init__(self, text="", message_id=1, new_chat_members=None):
        self.text = text
        self.message_id = message_id
        self.new_chat_members = new_chat_members or []
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, uid=111, chat=None, text="", message_id=1,
                 new_chat_members=None):
        self.effective_user = FakeUser(uid)
        self.effective_chat = chat or FakeChat(1001)
        self.message = FakeMessage(text, message_id, new_chat_members)


class FakeContext:
    def __init__(self, conn, bot):
        self.bot_data = {"db": conn}
        self.bot = bot


conn = db.init_db(":memory:")
grup.register(type("App", (), {"handlers": [],
                               "add_handler": lambda self, h: self.handlers.append(h)})(),
              conn)
_t("register: tabel grup_stats dibuat",
   conn.execute("SELECT name FROM sqlite_master WHERE name='grup_stats'").fetchone()
   is not None)

print("== welcome ==")
upd = FakeUpdate(chat=FakeChat(1001, "supergroup", "Grup Kopi"),
                 new_chat_members=[FakeNewMember("Budi")])
asyncio.run(grup.welcome(upd, FakeContext(conn, FakeBot())))
_t("welcome: teks sesuai",
   upd.message.replies == [
       "Halo Budi! Selamat datang di Grup Kopi 👋 "
       "Ketik /help buat lihat yang gue bisa."])
_t("welcome: cuma sapaan baru, bukan tag semua",
   all("@all" not in r and "@everyone" not in r for r in upd.message.replies))

print("== antispam badword ==")
grup._last_warn.clear()
bot = FakeBot()
upd = FakeUpdate(uid=111, text="kamu anjing banget", message_id=7)
asyncio.run(grup.antispam(upd, FakeContext(conn, bot)))
_t("badword: pesan dihapus", bot.deleted == [(1001, 7)])
_t("badword: peringatan dikirim sekali", len(upd.message.replies) == 1
   and "kata kasar" in upd.message.replies[0])

print("== antispam rate-limit peringatan ==")
grup._last_warn.clear()
bot = FakeBot()
upd1 = FakeUpdate(uid=111, text="anjing", message_id=8)
upd2 = FakeUpdate(uid=222, text="anjir", message_id=9)
asyncio.run(grup.antispam(upd1, FakeContext(conn, bot)))
asyncio.run(grup.antispam(upd2, FakeContext(conn, bot)))
_t("rate-limit: dua2nya dihapus", len(bot.deleted) == 2)
_t("rate-limit: peringatan cuma 1x dalam 60 detik",
   len(upd1.message.replies) == 1 and len(upd2.message.replies) == 0)

print("== antispam link ==")
# non-admin
bot = FakeBot(member_status="member")
upd = FakeUpdate(uid=111, text="cek https://example.com mantap", message_id=10)
asyncio.run(grup.antispam(upd, FakeContext(conn, bot)))
_t("link non-admin: dihapus", bot.deleted == [(1001, 10)])
# admin
bot = FakeBot(member_status="administrator")
upd = FakeUpdate(uid=999, text="cek https://example.com mantap", message_id=11)
asyncio.run(grup.antispam(upd, FakeContext(conn, bot)))
_t("link admin: TIDAK dihapus", bot.deleted == [])
# creator
bot = FakeBot(member_status="creator")
upd = FakeUpdate(uid=998, text="http://x.id", message_id=12)
asyncio.run(grup.antispam(upd, FakeContext(conn, bot)))
_t("link creator: TIDAK dihapus", bot.deleted == [])
# get_chat_member gagal -> fail-open
bot = FakeBot(member_status=RuntimeError("cek gagal"))
upd = FakeUpdate(uid=111, text="https://example.com", message_id=13)
asyncio.run(grup.antispam(upd, FakeContext(conn, bot)))
_t("link + cek admin gagal: fail-open, tidak dihapus", bot.deleted == [])
# pesan bersih
bot = FakeBot()
upd = FakeUpdate(uid=111, text="halo semua apa kabar", message_id=14)
asyncio.run(grup.antispam(upd, FakeContext(conn, bot)))
_t("pesan bersih: dibiarkan", bot.deleted == [] and upd.message.replies == [])

print("== statistik ==")
db.upsert_user(conn, 1, "si_aktif")
db.upsert_user(conn, 2, "si_ramai")
# user 3 tanpa username di tabel users
week = grup._week()
counts = {1: 12, 2: 30, 3: 5, 4: 50, 5: 20, 6: 1}  # user 6 keluar dari top 5
for uid, n in counts.items():
    conn.execute(
        "INSERT INTO grup_stats(chat_id, user_id, week, count) VALUES(1001,?,?,?)",
        (uid, week, n))
conn.commit()
upd = FakeUpdate(chat=FakeChat(1001, "supergroup", "Grup Kopi"))
asyncio.run(grup.statistik(upd, FakeContext(conn, FakeBot())))
got = upd.message.replies[0] if upd.message.replies else ""
_lines = got.splitlines()
_t("statistik: header top", _lines[:1] == ["🏆 Top chat minggu ini:"])
_t("statistik: top 5 urut menurun",
   _lines[1:] == ["1. user4 — 50 pesan",
                  "2. @si_ramai — 30 pesan",
                  "3. user5 — 20 pesan",
                  "4. @si_aktif — 12 pesan",
                  "5. user3 — 5 pesan"])
_t("statistik: cuma 5, bukan 6", len(_lines[1:]) == 5)
# grup lain tidak ikut
conn.execute("INSERT INTO grup_stats(chat_id, user_id, week, count)"
             " VALUES(2002,1,?,999)", (week,))
conn.commit()
upd = FakeUpdate(chat=FakeChat(1001, "supergroup", "Grup Kopi"))
asyncio.run(grup.statistik(upd, FakeContext(conn, FakeBot())))
_t("statistik: grup lain tidak bocor", "999" not in upd.message.replies[0])
# kosong
conn.execute("DELETE FROM grup_stats WHERE chat_id=3003")
upd = FakeUpdate(chat=FakeChat(3003, "supergroup", "Grup Sepi"))
asyncio.run(grup.statistik(upd, FakeContext(conn, FakeBot())))
_t("statistik: grup kosong pesan ramah",
   any("Belum ada" in r for r in upd.message.replies))
# private: diam
upd = FakeUpdate(chat=FakeChat(1001, "private"))
asyncio.run(grup.statistik(upd, FakeContext(conn, FakeBot())))
_t("statistik: private diam", upd.message.replies == [])

print("== /help grup ==")
upd = FakeUpdate(chat=FakeChat(1001, "group", "Grup Kopi"))
asyncio.run(grup.grup_help(upd, FakeContext(conn, FakeBot())))
_t("help: di grup nampilin fitur", len(upd.message.replies) == 1
   and "/statistik" in upd.message.replies[0])
upd = FakeUpdate(chat=FakeChat(1001, "private"))
asyncio.run(grup.grup_help(upd, FakeContext(conn, FakeBot())))
_t("help: di private diam total", upd.message.replies == [])

print("== count_stat ==")
conn.execute("DELETE FROM grup_stats WHERE chat_id=4004")
ctx = FakeContext(conn, FakeBot())
for _ in range(3):
    asyncio.run(grup.count_stat(FakeUpdate(uid=77, chat=FakeChat(4004)), ctx))
row = conn.execute("SELECT count FROM grup_stats WHERE chat_id=4004 AND user_id=77"
                   " AND week=?", (grup._week(),)).fetchone()
_t("count_stat: 3 pesan tercatat", row and row[0] == 3)

print("GRUP " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
