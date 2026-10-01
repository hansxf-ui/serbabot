"""Test plugin premium: Stars invoice, precheckout, paid success, status.

Semua di-mock (Telegram + DB in-memory). Tidak ada network sungguhan.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.premium as premium
from telegram import LabeledPrice

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester"


class FakeMessage:
    def __init__(self, chat_id=0):
        self.chat_id = chat_id
        self.replies = []  # (text, kwargs)

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return self


class FakeBot:
    def __init__(self):
        self.invoices = []

    async def send_invoice(self, **kw):
        self.invoices.append(kw)


class FakeQuery:
    def __init__(self, data, uid):
        self.data = data
        self.from_user = FakeUser(uid)
        self.message = FakeMessage(chat_id=uid)
        self.answered = []

    async def answer(self, *a, **kw):
        self.answered.append((a, kw))


class FakePreCheckout:
    def __init__(self, payload, uid):
        self.invoice_payload = payload
        self.from_user = FakeUser(uid)
        self.answers = []

    async def answer(self, ok, error_message=None):
        self.answers.append({"ok": ok, "error_message": error_message})


class FakePayment:
    def __init__(self, payload):
        self.invoice_payload = payload
        self.telegram_payment_charge_id = "ch_test_123"
        self.currency = "XTR"
        self.total_amount = 100


class FakeContext:
    def __init__(self, conn, bot=None):
        self.bot = bot or FakeBot()
        self.bot_data = {"db": conn}


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, h):
        self.handlers.append(h)


def cmd_update(uid):
    upd = type("U", (), {})()
    upd.effective_user = FakeUser(uid)
    upd.message = FakeMessage(chat_id=uid)
    return upd


def cb_update(data, uid):
    upd = type("U", (), {})()
    upd.callback_query = FakeQuery(data, uid)
    return upd


def pre_update(payload, uid):
    upd = type("U", (), {})()
    upd.pre_checkout_query = FakePreCheckout(payload, uid)
    return upd


def paid_update(payload, uid):
    upd = type("U", (), {})()
    upd.effective_user = FakeUser(uid)
    msg = FakeMessage(chat_id=uid)
    msg.successful_payment = FakePayment(payload)
    upd.message = msg
    return upd


def buttons_of(reply_kw):
    kb = reply_kw.get("reply_markup")
    if not kb:
        return []
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


print("== premium ==")

conn = db.init_db(":memory:")

# register() jalan + bikin tabel premium_payments
app = FakeApp()
premium.register(app, conn)
_t("register: 4 handler terdaftar", len(app.handlers) == 4)
_t("register: tabel premium_payments ada",
   conn.execute("SELECT name FROM sqlite_master WHERE name='premium_payments'"
                ).fetchone() is not None)

# 1. /premium: 2 tier + harga benar, tombol callback benar
upd = cmd_update(111)
asyncio.run(premium.premium_cmd(upd, FakeContext(conn)))
text, kw = upd.message.replies[0]
btns = buttons_of(kw)
_t("/premium: 2 tier ditampilkan", len(btns) == 2)
_t("/premium: tier 50 (Rp12rb) callback benar",
   any(c == "premium:buy:50" and "50" in t and "Rp12rb" in t for t, c in btns))
_t("/premium: tier 100 (Rp23rb) callback benar",
   any(c == "premium:buy:100" and "100" in t and "Rp23rb" in t for t, c in btns))
_t("/premium: ada perbandingan gratis vs premium",
   "Gratis vs Premium" in text and "unlimited" in text)

# 2. callback -> invoice Stars benar
upd2 = cb_update("premium:buy:100", 111)
bot2 = FakeBot()
asyncio.run(premium.buy_cb(upd2, FakeContext(conn, bot2)))
inv = bot2.invoices[0]
_t("invoice: 1 invoice terkirim", len(bot2.invoices) == 1)
_t("invoice: currency XTR", inv["currency"] == "XTR")
_t("invoice: provider_token kosong", inv["provider_token"] == "")
_t("invoice: payload premium:<n>:<user_id>",
   inv["payload"] == "premium:100:111")
_t("invoice: title benar", inv["title"] == "SerbaBot Premium 30 hari")
_t("invoice: LabeledPrice 100 Stars",
   len(inv["prices"]) == 1
   and isinstance(inv["prices"][0], LabeledPrice)
   and inv["prices"][0].amount == 100)
_t("invoice: chat_id user pemencet", inv["chat_id"] == 111)

# 2b. callback tier 50 juga benar
upd2b = cb_update("premium:buy:50", 222)
bot2b = FakeBot()
asyncio.run(premium.buy_cb(upd2b, FakeContext(conn, bot2b)))
_t("invoice tier 50: payload + harga benar",
   bot2b.invoices[0]["payload"] == "premium:50:222"
   and bot2b.invoices[0]["prices"][0].amount == 50)

# 2c. send_invoice gagal -> pesan jujur
class BoomBot(FakeBot):
    async def send_invoice(self, **kw):
        raise RuntimeError("Stars API down")

upd2c = cb_update("premium:buy:50", 333)
asyncio.run(premium.buy_cb(upd2c, FakeContext(conn, BoomBot())))
_t("invoice gagal: pesan jujur ke user",
   any("gagal" in r[0].lower() for r in upd2c.callback_query.message.replies))

# 3. precheckout valid -> ok=True
upd3 = pre_update("premium:100:111", 111)
ok = asyncio.run(premium.precheckout(upd3, FakeContext(conn)))
_t("precheckout valid: ok=True",
   ok is True and upd3.pre_checkout_query.answers == [{"ok": True, "error_message": None}])

# 3b. payload asing -> ok=False
upd3b = pre_update("topup:50:111", 111)
okb = asyncio.run(premium.precheckout(upd3b, FakeContext(conn)))
_t("precheckout payload asing: ok=False",
   okb is False and upd3b.pre_checkout_query.answers[0]["ok"] is False
   and upd3b.pre_checkout_query.answers[0]["error_message"])

# 3c. user_id beda -> ok=False
upd3c = pre_update("premium:100:111", 999)
okc = asyncio.run(premium.precheckout(upd3c, FakeContext(conn)))
_t("precheckout user beda: ok=False",
   okc is False and upd3c.pre_checkout_query.answers[0]["ok"] is False)

# 4. successful_payment -> grant 30 hari + tercatat + balasan benar
upd4 = paid_update("premium:100:444", 444)
asyncio.run(premium.paid_success(upd4, FakeContext(conn)))
_t("paid: user jadi premium", db.is_premium(conn, 444))
rows = conn.execute(
    "SELECT user_id, stars, telegram_payment_charge_id FROM premium_payments"
).fetchall()
_t("paid: tercatat di premium_payments",
   len(rows) == 1 and rows[0] == (444, 100, "ch_test_123"))
until = db.premium_until(conn, 444)
import datetime
sisa = (datetime.datetime.fromisoformat(until) - datetime.datetime.now()).days
_t("paid: premium 30 hari (grant 30)", 29 <= sisa <= 30)
_t("paid: balasan benar",
   any("🎉" in r[0] and "30 hari" in r[0] and "Makasih" in r[0]
       for r in upd4.message.replies))

# 4b. beli lagi = numpuk dari sisa
until1 = db.premium_until(conn, 444)
asyncio.run(premium.paid_success(paid_update("premium:50:444", 444), FakeContext(conn)))
until2 = db.premium_until(conn, 444)
_t("paid: beli ulang numpuk (+30 hari dari sisa)",
   (datetime.datetime.fromisoformat(until2)
    - datetime.datetime.fromisoformat(until1)).days == 30)
_t("paid: 2 pembayaran tercatat",
   conn.execute("SELECT COUNT(*) FROM premium_payments WHERE user_id=444"
                ).fetchone()[0] == 2)

# 5. user premium existing -> /premium tampilkan tanggal aktif
db.upsert_user(conn, 555, "vip")
db.grant_premium_days(conn, 555, 10)
upd5 = cmd_update(555)
asyncio.run(premium.premium_cmd(upd5, FakeContext(conn)))
text5 = upd5.message.replies[0][0]
sampai5 = premium._fmt_tanggal(db.premium_until(conn, 555))
_t("premium existing: tampilkan 'aktif sampai'",
   "aktif sampai" in text5.lower())
_t("premium existing: tanggalnya benar", sampai5 in text5)
_t("premium existing: tombol perpanjang ada",
   any(c.startswith("premium:buy:") for _, c in buttons_of(upd5.message.replies[0][1])))

# 6. tidak ada teks QRIS/DANA di plugin premium
import pathlib
src = pathlib.Path(premium.__file__).read_text()
_t("plugin premium: tanpa kata QRIS/DANA",
   "QRIS" not in src and "DANA" not in src)

print("PREMIUM " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
