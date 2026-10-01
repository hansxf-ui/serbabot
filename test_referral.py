"""Test plugin referral: credit_referral (L1/L2/milestone/duplikat/self-ref)
+ /invite + pola hook /start (anonchat).

Semua di-mock (Telegram). Tidak ada network sungguhan.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.referral as referral
import plugins.anonchat as anonchat

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeMe:
    username = "serbabot"


class FakeBot:
    async def get_me(self):
        return FakeMe()


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
    def __init__(self, conn, args=None):
        self.args = args or []
        self.bot = FakeBot()
        self.bot_data = {"db": conn, "admin_id": None}


def fresh_conn():
    c = db.init_db(":memory:")
    referral.register(type("A", (), {"add_handler": lambda self, h: None})(), c)
    return c


print("== referral (credit_referral) ==")

# 1. referral dasar: L1 +100, tercatat
conn = fresh_conn()
referral.credit_referral(conn, 2, 1)
_t("L1: tercatat", conn.execute(
    "SELECT referrer_id FROM referrals WHERE referred_id=2").fetchone() == (1,))
_t("L1: +100 koin buat referrer", db.get_coins(conn, 1) == 100)

# 2. berjenjang: 1->2, 2->3 => 2 dapat L1 (+100), 1 dapat L2 (+20)
referral.credit_referral(conn, 3, 2)
_t("L2: user 2 dapat +100 (total 100)", db.get_coins(conn, 2) == 100)
_t("L2: user 1 dapat +20 level 2 (total 120)", db.get_coins(conn, 1) == 120)

# 3. duplikat user baru: diabaikan (tidak double koin)
referral.credit_referral(conn, 2, 9)
_t("duplikat: user baru sama tidak dihitung 2x",
   conn.execute("SELECT COUNT(*) FROM referrals WHERE referred_id=2").fetchone()[0] == 1)
_t("duplikat: referrer kedua tidak dapat koin", db.get_coins(conn, 9) == 0)

# 4. self-referral: diabaikan
referral.credit_referral(conn, 7, 7)
_t("self-ref: diabaikan",
   conn.execute("SELECT COUNT(*) FROM referrals WHERE referred_id=7").fetchone()[0] == 0)
_t("self-ref: tidak dapat koin", db.get_coins(conn, 7) == 0)

# 5. milestone: tepat 5 teman -> 7 hari premium + sekali klaim
conn2 = fresh_conn()
for new in range(20, 25):
    referral.credit_referral(conn2, new, 10)
_t("milestone: 5 referral tercatat",
   conn2.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=10").fetchone()[0] == 5)
_t("milestone: +500 koin (5x100)", db.get_coins(conn2, 10) == 500)
_t("milestone: premium aktif", db.is_premium(conn2, 10))
_t("milestone: ditandai claimed",
   conn2.execute("SELECT COUNT(*) FROM referrals_milestone WHERE user_id=10")
   .fetchone()[0] == 1)
until_before = db.premium_until(conn2, 10)
referral.credit_referral(conn2, 25, 10)  # teman ke-6
_t("milestone: klaim tidak dobel (premium tidak diperpanjang)",
   db.premium_until(conn2, 10) == until_before
   and conn2.execute("SELECT COUNT(*) FROM referrals_milestone WHERE user_id=10")
   .fetchone()[0] == 1)
_t("milestone: koin tetap masuk untuk teman ke-6", db.get_coins(conn2, 10) == 600)

print("== referral (/invite) ==")

# 6. /invite: deep link benar + statistik + progress milestone
conn3 = fresh_conn()
referral.credit_referral(conn3, 31, 30)
referral.credit_referral(conn3, 32, 30)
upd = FakeUpdate(30)
asyncio.run(referral.invite_cmd(upd, FakeContext(conn3)))
_t("invite: 1 balasan", len(upd.message.replies) == 1)
msg = upd.message.replies[0]
_t("invite: deep link benar",
   "https://t.me/serbabot?start=referral_30" in msg)
_t("invite: statistik L1 benar", "👥 Teman yang udah join: 2" in msg)
_t("invite: total koin referral benar",
   "🪙 Total koin dari referral: 200" in msg)
_t("invite: progress milestone", "🏆 Ajak 2/5 teman → 7 hari Premium gratis!" in msg)

print("== hook /start (anonchat) ==")

# 7. /start dengan referral_7 -> referral tercatat + WELCOME tetap terkirim
conn4 = db.init_db(":memory:")
referral.register(type("A", (), {"add_handler": lambda self, h: None})(), conn4)
upd4 = FakeUpdate(8)
asyncio.run(anonchat.start(upd4, FakeContext(conn4, ["referral_7"])))
_t("hook: referral tercatat via /start",
   conn4.execute("SELECT referrer_id FROM referrals WHERE referred_id=8")
   .fetchone() == (7,))
_t("hook: referrer dapat +100", db.get_coins(conn4, 7) == 100)
_t("hook: WELCOME tetap terkirim",
   upd4.message.replies == [anonchat.WELCOME])

# 8. /start tanpa args -> WELCOME saja, tidak ada referral
upd5 = FakeUpdate(9)
asyncio.run(anonchat.start(upd5, FakeContext(conn4, [])))
_t("tanpa args: WELCOME terkirim", upd5.message.replies == [anonchat.WELCOME])
_t("tanpa args: tidak ada referral",
   conn4.execute("SELECT COUNT(*) FROM referrals WHERE referred_id=9").fetchone()[0] == 0)

# 9. args invalid (bukan angka) -> WELCOME tetap jalan, referral diabaikan
upd6 = FakeUpdate(10)
asyncio.run(anonchat.start(upd6, FakeContext(conn4, ["referral_abc"])))
_t("args invalid: WELCOME tetap terkirim", upd6.message.replies == [anonchat.WELCOME])
_t("args invalid: referral diabaikan",
   conn4.execute("SELECT COUNT(*) FROM referrals WHERE referred_id=10").fetchone()[0] == 0)

# 10. self-referral via hook -> diabaikan
upd7 = FakeUpdate(11)
asyncio.run(anonchat.start(upd7, FakeContext(conn4, ["referral_11"])))
_t("self-ref via hook: diabaikan",
   conn4.execute("SELECT COUNT(*) FROM referrals WHERE referred_id=11").fetchone()[0] == 0)

print("REFERRAL " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
