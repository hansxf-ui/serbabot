"""Test plugin menfess: filter kata kasar, counter, env kosong, quota.

Semua di-mock (Telegram + env). Tidak ada network sungguhan.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.menfess as menfess
from plugins.anonchat.filtering import load_badwords

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeBot:
    def __init__(self):
        self.sent = []          # (chat_id, text)
        self.raise_on_send = False

    async def send_message(self, chat_id, text, **kw):
        if self.raise_on_send:
            raise RuntimeError("channel down")
        self.sent.append((chat_id, text))
        return None


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
    def __init__(self, conn, args, bot):
        self.args = args
        self.bot = bot
        self.bot_data = {"db": conn, "admin_id": None}


CHANNEL = "-100123456789"
BAD = next(iter(load_badwords()))  # kata nyata dari badwords.txt, bukan hardcode

print("== menfess ==")
conn = db.init_db(":memory:")
menfess.register(type("A", (), {"add_handler": lambda self, h: None})(), conn)

# 1. tanpa argumen -> cara pakai, channel aman, quota aman
os.environ["MENFESS_CHANNEL_ID"] = CHANNEL
bot = FakeBot()
upd = FakeUpdate(111)
asyncio.run(menfess.menfess_cmd(upd, FakeContext(conn, [], bot)))
_t("tanpa argumen: kasih cara pakai",
   any("/menfess <teks>" in r for r in upd.message.replies))
_t("tanpa argumen: channel tidak dikirim", bot.sent == [])
_t("tanpa argumen: quota tidak kepotong",
   db.get_quota_today(conn, "menfess", 111) == 0)

# 2. kata kasar -> ditolak santai, channel aman, quota aman
bot.sent.clear()
upd2 = FakeUpdate(222)
asyncio.run(menfess.menfess_cmd(upd2, FakeContext(conn, [BAD, "banget"], bot)))
_t("badword: ditolak", any("filter kata kasar" in r for r in upd2.message.replies))
_t("badword: channel tidak dikirim", bot.sent == [])
_t("badword: quota tidak kepotong",
   db.get_quota_today(conn, "menfess", 222) == 0)

# 3. env kosong -> JUJUR (tidak fake kirim)
del os.environ["MENFESS_CHANNEL_ID"]
bot.sent.clear()
upd3 = FakeUpdate(333)
asyncio.run(menfess.menfess_cmd(upd3, FakeContext(conn, ["halo", "semua"], bot)))
_t("env kosong: pesan jujur ke user",
   any("MENFESS_CHANNEL_ID" in r for r in upd3.message.replies))
_t("env kosong: channel tidak dikirim", bot.sent == [])
_t("env kosong: quota tidak kepotong",
   db.get_quota_today(conn, "menfess", 333) == 0)
os.environ["MENFESS_CHANNEL_ID"] = CHANNEL

# 4. sukses -> format post benar, counter #1, quota kepotong, tercatat
bot.sent.clear()
upd4 = FakeUpdate(444)
asyncio.run(menfess.menfess_cmd(upd4, FakeContext(conn, ["halo", "semua"], bot)))
_t("sukses: 1 pesan ke channel",
   len(bot.sent) == 1 and bot.sent[0][0] == CHANNEL)
_t("sukses: format post anonim",
   bot.sent[0][1] == "📮 Menfess #1\n\nhalo semua")
_t("sukses: konfirmasi ke user",
   any("Menfess #1 terkirim" in r for r in upd4.message.replies))
_t("sukses: quota kepotong 1",
   db.get_quota_today(conn, "menfess", 444) == 1)
_t("sukses: tercatat di tabel menfess",
   conn.execute("SELECT sender_id, text, counter FROM menfess").fetchone()
   == (444, "halo semua", 1))

# 5. counter naik per post
upd5 = FakeUpdate(555)
asyncio.run(menfess.menfess_cmd(upd5, FakeContext(conn, ["kedua"], bot)))
_t("counter: post kedua = #2", bot.sent[-1][1] == "📮 Menfess #2\n\nkedua")

# 6. quota habis (3) -> ditolak, channel aman
for _ in range(3):
    db.inc_quota_today(conn, "menfess", 666)
upd6 = FakeUpdate(666)
n_sent = len(bot.sent)
asyncio.run(menfess.menfess_cmd(upd6, FakeContext(conn, ["spam"], bot)))
_t("quota habis: ditolak", any("habis" in r for r in upd6.message.replies))
_t("quota habis: channel tidak dikirim", len(bot.sent) == n_sent)

# 7. VIP -> unlimited walau quota penuh, quota tidak dicatat
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(777,'v',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
for _ in range(3):
    db.inc_quota_today(conn, "menfess", 777)
n_sent = len(bot.sent)
upd7 = FakeUpdate(777)
asyncio.run(menfess.menfess_cmd(upd7, FakeContext(conn, ["vip", "post"], bot)))
_t("vip: tetap terkirim", len(bot.sent) == n_sent + 1)
_t("vip: quota tidak dicatat", db.get_quota_today(conn, "menfess", 777) == 3)

# 8. kirim ke channel gagal -> pesan jujur, quota tidak kepotong, tanpa counter
bot.raise_on_send = True
upd8 = FakeUpdate(888)
asyncio.run(menfess.menfess_cmd(upd8, FakeContext(conn, ["halo"], bot)))
_t("kirim gagal: pesan jujur", any("Gagal ngirim" in r for r in upd8.message.replies))
_t("kirim gagal: quota tidak kepotong",
   db.get_quota_today(conn, "menfess", 888) == 0)
_t("kirim gagal: tidak tercatat di tabel",
   conn.execute("SELECT COUNT(*) FROM menfess WHERE sender_id=888").fetchone()[0] == 0)

del os.environ["MENFESS_CHANNEL_ID"]
print("MENFESS " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
