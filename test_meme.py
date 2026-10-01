"""Test plugin meme: make_meme, watermark VIP, cara pakai, stiker, quota.

Semua di-mock (Telegram). Tidak ada network sungguhan.
"""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from PIL import Image

import plugins.meme as meme

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


def _test_photo(w=800, h=600, color=(180, 60, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def _region_differs(a, b, box):
    ia = Image.open(io.BytesIO(a)).convert("RGB").crop(box)
    ib = Image.open(io.BytesIO(b)).convert("RGB").crop(box)
    return list(ia.getdata()) != list(ib.getdata())


# ---------- fake Telegram ----------

class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester"
        self.first_name = "Tester"


class FakePhoto:
    def __init__(self, data):
        self._data = data

    async def get_file(self):
        return self

    async def download_as_bytearray(self):
        return bytearray(self._data)


class FakeMessage:
    def __init__(self, own_photo_data=None, caption=None):
        self.replies = []
        self.reply_kws = []
        self.photos = []
        self.stickers = []
        self.reply_to_message = None
        self.caption = caption
        # foto yang dikirim bareng caption perintah (tanpa reply)
        self.photo = [FakePhoto(own_photo_data)] if own_photo_data else []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        self.reply_kws.append(kw)

    async def reply_photo(self, photo, **kw):
        self.photos.append(photo)

    async def reply_sticker(self, sticker, **kw):
        if hasattr(sticker, "seek"):
            sticker.seek(0)
        self.stickers.append(sticker.read())


class FakeReplyMsg:
    def __init__(self, data):
        self.photo = [FakePhoto(data)]


class FakeUpdate:
    def __init__(self, uid, photo_data=None, own_photo_data=None, caption=None):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage(own_photo_data, caption)
        if photo_data is not None:
            self.message.reply_to_message = FakeReplyMsg(photo_data)


class FakeMe:
    username = "testbot"


class FakeStickerSet:
    def __init__(self, name, title, n):
        self.name = name
        self.title = title
        self.stickers = [object() for _ in range(n)]


class FakeBot:
    def __init__(self):
        self.calls = []

    async def get_me(self):
        return FakeMe()

    async def get_sticker_set(self, name):
        return FakeStickerSet(name, "Stiker Test", 8)

    async def set_sticker_set_title(self, name, title):
        self.title_touched = (name, title)
        return True

    async def create_new_sticker_set(self, user_id, name, title, stickers, **kw):
        self.calls.append(("create", user_id, name, title, stickers))
        return True

    async def add_sticker_to_set(self, user_id, name, sticker, **kw):
        self.calls.append(("add", user_id, name, sticker))
        return True


class FakeContext:
    def __init__(self, conn, args=None, bot=None, admin_id=None):
        self.args = args or []
        self.bot_data = {"db": conn, "admin_id": admin_id}
        self.bot = bot


print("== meme ==")

photo = _test_photo()

# 1. make_meme: PNG valid, teks atas & bawah kepasang
out = meme.make_meme(photo, "teks atas", "teks bawah", watermark=False)
im = Image.open(out)
_t("make_meme: PNG valid", im.format == "PNG" and im.size == (800, 600))
w, h = im.size
_t("make_meme: teks atas kepasang",
   _region_differs(photo, out.getvalue(), (0, 0, w, h // 4)))
_t("make_meme: teks bawah kepasang",
   _region_differs(photo, out.getvalue(), (0, 3 * h // 4, w, h)))

# 2. watermark: ada untuk non-VIP, tidak ada untuk VIP
wm = meme.make_meme(photo, "", "", watermark=True).getvalue()
nowm = meme.make_meme(photo, "", "", watermark=False).getvalue()
_t("watermark non-VIP: pojok kanan bawah berubah",
   _region_differs(nowm, wm, (w - 160, h - 60, w, h)))
_t("tanpa watermark: identik dengan foto asli",
   not _region_differs(photo, nowm, (w - 160, h - 60, w, h)))

# 3. /meme tanpa reply foto -> cara pakai
conn = db.init_db(":memory:")
upd = FakeUpdate(111)
asyncio.run(meme.meme_cmd(upd, FakeContext(conn, ["A|B"])))
_t("tanpa reply foto: kasih cara pakai",
   any("/meme TEKS ATAS|TEKS BAWAH" in r for r in upd.message.replies))
_t("tanpa reply foto: tidak kirim foto", upd.message.photos == [])
_t("tanpa reply foto: jatah aman", db.get_quota_today(conn, "meme", 111) == 0)

# 4. /meme sukses: foto dikirim, jatah kepotong 1
conn = db.init_db(":memory:")
upd = FakeUpdate(222, photo)
asyncio.run(meme.meme_cmd(upd, FakeContext(conn, ["atas", "|", "bawah"])))
_t("/meme sukses: foto dikirim", len(upd.message.photos) == 1)
im2 = Image.open(upd.message.photos[0])
_t("/meme sukses: PNG valid", im2.format == "PNG")
_t("/meme sukses: jatah kepotong 1", db.get_quota_today(conn, "meme", 222) == 1)

# 5. /meme quota: 10/hari, ke-11 ditolak
conn = db.init_db(":memory:")
ctx = FakeContext(conn, ["a|b"])
for _ in range(10):
    asyncio.run(meme.meme_cmd(FakeUpdate(333, photo), ctx))
upd5 = FakeUpdate(333, photo)
asyncio.run(meme.meme_cmd(upd5, ctx))
_t("/meme ke-11: ditolak (habis)",
   any("habis" in r for r in upd5.message.replies))
_t("/meme ke-11: tidak kirim foto", upd5.message.photos == [])
_t("quota tercatat 10", db.get_quota_today(conn, "meme", 333) == 10)

# 6. VIP: unlimited, quota tidak dicatat
conn = db.init_db(":memory:")
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(666,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
ctx6 = FakeContext(conn, ["a|b"])
ok = 0
for _ in range(12):
    u = FakeUpdate(666, photo)
    asyncio.run(meme.meme_cmd(u, ctx6))
    ok += len(u.message.photos)
_t("VIP: 12x /meme semua dilayani", ok == 12)
_t("VIP: quota tidak dicatat", db.get_quota_today(conn, "meme", 666) == 0)

# 7. photo_to_sticker: sisi terpanjang 512, jaga rasio
st = meme.photo_to_sticker(_test_photo(800, 600)).getvalue()
ims = Image.open(io.BytesIO(st))
_t("stiker 800x600 -> 512x384", ims.size == (512, 384) and ims.format == "PNG")
st2 = meme.photo_to_sticker(_test_photo(100, 50)).getvalue()
ims2 = Image.open(io.BytesIO(st2))
_t("stiker kecil 100x50 -> upscale 512x256", ims2.size == (512, 256))

# 8. /stiker flow (simpel, tanpa pack): stiker dikirim, pesan sukses
conn = db.init_db(":memory:")
bot = FakeBot()
ctx8 = FakeContext(conn, bot=bot)
u1 = FakeUpdate(777, photo)
asyncio.run(meme.stiker_cmd(u1, ctx8))
_t("/stiker pertama: tidak panggil API set (tanpa pack)",
   bot.calls == [])
_t("/stiker pertama: stiker dikirim ke chat",
   len(u1.message.stickers) == 1)
_stback = Image.open(io.BytesIO(u1.message.stickers[0]))
_t("/stiker pertama: stiker di chat PNG 512 valid",
   max(_stback.size) == 512 and _stback.format == "PNG")
_t("/stiker pertama: pesan sukses",
   any("Stiker jadi" in r for r in u1.message.replies))

u2 = FakeUpdate(777, photo)
asyncio.run(meme.stiker_cmd(u2, ctx8))
_t("/stiker kedua: stiker dikirim ke chat",
   len(u2.message.stickers) == 1)
_t("/stiker: jatah kepotong 2", db.get_quota_today(conn, "stiker", 777) == 2)

# 9. /stiker tanpa reply foto -> cara pakai
conn = db.init_db(":memory:")
upd9 = FakeUpdate(888)
asyncio.run(meme.stiker_cmd(upd9, FakeContext(conn, bot=FakeBot())))
_t("/stiker tanpa reply: kasih cara pakai",
   any("/stiker" in r for r in upd9.message.replies))

# 10. /stiker quota: 5/hari, ke-6 ditolak
conn = db.init_db(":memory:")
ctx10 = FakeContext(conn, bot=FakeBot())
for _ in range(5):
    asyncio.run(meme.stiker_cmd(FakeUpdate(999, photo), ctx10))
u10 = FakeUpdate(999, photo)
asyncio.run(meme.stiker_cmd(u10, ctx10))
_t("/stiker ke-6: ditolak (habis)",
   any("habis" in r for r in u10.message.replies))
_t("quota stiker tercatat 5", db.get_quota_today(conn, "stiker", 999) == 5)

# 11. /stiker via caption: kirim foto + caption /stiker (tanpa reply)
conn = db.init_db(":memory:")
bot11 = FakeBot()
u11 = FakeUpdate(444, own_photo_data=photo)
asyncio.run(meme.stiker_cmd(u11, FakeContext(conn, bot=bot11)))
_t("/stiker caption: stiker dikirim ke chat",
   len(u11.message.stickers) == 1)
_t("/stiker caption: jatah kepotong 1",
   db.get_quota_today(conn, "stiker", 444) == 1)

# 12. /meme via caption: kirim foto + caption /meme a|b (tanpa reply)
conn = db.init_db(":memory:")
u12 = FakeUpdate(555, own_photo_data=photo)
asyncio.run(meme.meme_cmd(u12, FakeContext(conn, ["a|b"])))
_t("/meme caption: foto meme dikirim", len(u12.message.photos) == 1)
_t("/meme caption: jatah kepotong 1", db.get_quota_today(conn, "meme", 555) == 1)

# 13. reply tetap prioritas kalau dua-duanya ada
conn = db.init_db(":memory:")
bot13 = FakeBot()
u13 = FakeUpdate(666, photo_data=photo, own_photo_data=_test_photo(100, 50))
asyncio.run(meme.stiker_cmd(u13, FakeContext(conn, bot=bot13)))
_t("/stiker: foto reply yang dipakai (bukan caption)",
   Image.open(io.BytesIO(u13.message.stickers[0])).size == (512, 384))

# 14. caption router: foto + caption /stiker -> jalan (ini yang kemarin mati)
conn = db.init_db(":memory:")
bot14 = FakeBot()
u14 = FakeUpdate(1010, own_photo_data=photo, caption="/stiker")
asyncio.run(meme._caption_router(u14, FakeContext(conn, bot=bot14)))
_t("caption /stiker: tidak panggil API set",
   bot14.calls == [])
_t("caption /stiker: stiker dikirim ke chat",
   len(u14.message.stickers) == 1)
_t("caption /stiker: jatah kepotong 1",
   db.get_quota_today(conn, "stiker", 1010) == 1)

# 15. caption router: /meme dengan argumen di caption
conn = db.init_db(":memory:")
u15 = FakeUpdate(2020, own_photo_data=photo, caption="/meme ATAS|BAWAH")
asyncio.run(meme._caption_router(u15, FakeContext(conn)))
_t("caption /meme: foto meme dikirim", len(u15.message.photos) == 1)
_t("caption /meme: jatah kepotong 1",
   db.get_quota_today(conn, "meme", 2020) == 1)

# 16. caption router: caption biasa (bukan perintah) diabaikan
conn = db.init_db(":memory:")
bot16 = FakeBot()
u16 = FakeUpdate(3030, own_photo_data=photo, caption="liburan kemarin")
asyncio.run(meme._caption_router(u16, FakeContext(conn, bot=bot16)))
_t("caption biasa: tidak ngapa-ngapain",
   bot16.calls == [] and u16.message.replies == []
   and u16.message.stickers == [])

# 17. caption router: /stiker@namabot tetap jalan
conn = db.init_db(":memory:")
bot17 = FakeBot()
u17 = FakeUpdate(4040, own_photo_data=photo, caption="/stiker@testbot")
asyncio.run(meme._caption_router(u17, FakeContext(conn, bot=bot17)))
_t("caption /stiker@bot: stiker dikirim", len(u17.message.stickers) == 1)

print("MEME " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
