"""Test plugin paidmedia: katalog, payload, successful_payment, kirim ulang.

Semua di-mock (PIL asli, Telegram palsu). Tidak ada network.
"""
import asyncio
import os
import sys
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.paidmedia as pm
from PIL import Image

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


class FakeChat:
    def __init__(self, cid):
        self.id = cid


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.markup = None
        self.chat = FakeChat(99)
        self.successful_payment = None

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        self.markup = kw.get("reply_markup")


class FakeCallbackQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message

    async def answer(self):
        pass


class FakeSP:
    def __init__(self, payload, amount, currency="XTR"):
        self.invoice_payload = payload
        self.total_amount = amount
        self.currency = currency


class FakeBot:
    def __init__(self):
        self.paid_calls = []
        self.photos = []
        self.docs = []

    async def send_paid_media(self, **kw):
        self.paid_calls.append(kw)

    async def send_photo(self, chat_id, photo, **kw):
        self.photos.append((chat_id, photo))

    async def send_document(self, chat_id, document, **kw):
        self.docs.append((chat_id, document))


class FakeContext:
    def __init__(self, conn, bot):
        self.bot = bot
        self.bot_data = {"db": conn}


class FakeUpdate:
    def __init__(self, uid, callback=None, message=None):
        self.effective_user = FakeUser(uid)
        self.effective_chat = FakeChat(uid * 10)
        self.callback_query = callback
        self.message = message


def _btns(markup):
    return [(b.text, b.callback_data)
            for row in markup.inline_keyboard for b in row]


print("== paidmedia ==")

conn = db.init_db(":memory:")
bot = FakeBot()
ctx = FakeContext(conn, bot)

# 1. generator konten: dimensi & format benar
for i in range(3):
    w = pm.gen_wallpaper(i)
    img = Image.open(BytesIO(w.getvalue()))
    _t(f"wallpaper {i}: PNG 1080x1920", img.format == "PNG" and img.size == (1080, 1920))
for i in range(5):
    s = pm.gen_sticker(i)
    img = Image.open(BytesIO(s.getvalue()))
    _t(f"stiker {i}: PNG 512x512 RGBA", img.format == "PNG" and img.size == (512, 512))
eb = pm.gen_ebook_bytes()
_teks = eb.decode("utf-8")
_t("ebook: judul + 10 trik", "TRIK TELEGRAM" in _teks and "10." in _teks)

# 2. /katalog: 3 item + harga
msg = FakeMessage()
asyncio.run(pm.katalog_cmd(FakeUpdate(111, message=msg), ctx))
teks = "\n".join(msg.replies)
_t("katalog: 3 nama item",
   all(n in teks for n in ("Pack Wallpaper AI Sunset", "Pack Stiker Premium",
                           "E-book Mini: Trik Telegram")))
_t("katalog: harga 25/40/15", all(h in teks for h in ("⭐25", "⭐40", "⭐15")))
btns = _btns(msg.markup)
_t("katalog: 3 tombol beli",
   ("Beli ⭐25", "paidmedia:buy:wallpaper") in btns
   and ("Beli ⭐40", "paidmedia:buy:stiker") in btns
   and ("Beli ⭐15", "paidmedia:buy:ebook") in btns)

# 3. beli: payload format benar, media lengkap
msg2 = FakeMessage()
upd2 = FakeUpdate(222, callback=FakeCallbackQuery("paidmedia:buy:wallpaper", msg2))
asyncio.run(pm.buy_cb(upd2, ctx))
_t("beli: 1x send_paid_media", len(bot.paid_calls) == 1)
call = bot.paid_calls[0]
_t("beli: payload paidmedia:<item>:<user>",
   call.get("payload") == "paidmedia:wallpaper:222")
_t("beli: star_count 25", call.get("star_count") == 25)
_t("beli: 3 media foto", len(call.get("media", [])) == 3)
from telegram import InputPaidMediaPhoto
_t("beli: media bertipe InputPaidMediaPhoto",
   all(isinstance(m, InputPaidMediaPhoto) for m in call["media"]))

# 4. successful_payment: pembelian dicatat + konten dikirim ulang gratis
bot2 = FakeBot()
ctx2 = FakeContext(conn, bot2)
msg3 = FakeMessage()
msg3.successful_payment = FakeSP("paidmedia:stiker:333", 40)
upd3 = FakeUpdate(333, message=msg3)
upd3.effective_chat = FakeChat(777)
asyncio.run(pm.paid_ok(upd3, ctx2))
_t("bayar: pembelian tercatat", pm.has_bought(conn, 333, "stiker"))
_t("bayar: 5 stiker dikirim gratis", len(bot2.photos) == 5)
_t("bayar: tanpa send_paid_media lagi (gratis)", bot2.paid_calls == [])
_t("bayar: balasan mantap", any("Mantap" in r for r in msg3.replies))

# 5. item sudah dibeli -> /katalog tampil "dimiliki" + tombol kirim ulang
msg4 = FakeMessage()
asyncio.run(pm.katalog_cmd(FakeUpdate(333, message=msg4), ctx2))
teks4 = "\n".join(msg4.replies)
_t("katalog: status dimiliki", "✅" in teks4 and "dimiliki" in teks4)
btns4 = _btns(msg4.markup)
_t("katalog: tombol kirim ulang (bukan beli)",
   ("📩 Kirim ulang", "paidmedia:resend:stiker") in btns4
   and not any("buy:stiker" in cd for _, cd in btns4))

# 6. kirim ulang via callback: gratis, tanpa Stars
bot3 = FakeBot()
ctx3 = FakeContext(conn, bot3)
msg5 = FakeMessage()
upd5 = FakeUpdate(333, callback=FakeCallbackQuery("paidmedia:resend:stiker", msg5))
asyncio.run(pm.resend_cb(upd5, ctx3))
_t("resend: 5 stiker kekirim", len(bot3.photos) == 5)
_t("resend: tanpa bayar Stars", bot3.paid_calls == [])

# 7. resend item yang belum dibeli -> ditolak
msg6 = FakeMessage()
upd6 = FakeUpdate(444, callback=FakeCallbackQuery("paidmedia:resend:ebook", msg6))
asyncio.run(pm.resend_cb(upd6, ctx))
_t("resend belum beli: ditolak", any("belum beli" in r for r in msg6.replies))
_t("resend belum beli: tidak ada media", bot.docs == [] and bot.photos == [])

# 8. payload asing / item ngawur -> diabaikan, tidak tercatat
msg7 = FakeMessage()
msg7.successful_payment = FakeSP("ngawur:banget", 10)
asyncio.run(pm.paid_ok(FakeUpdate(555, message=msg7), ctx))
_t("payload asing: diabaikan", msg7.replies == [])
msg8 = FakeMessage()
msg8.successful_payment = FakeSP("paidmedia:ngawur:555", 10)
asyncio.run(pm.paid_ok(FakeUpdate(555, message=msg8), ctx))
_t("item ngawur: diabaikan", msg8.replies == [])

print("PAIDMEDIA " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
