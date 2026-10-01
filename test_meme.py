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
    def __init__(self):
        self.replies = []
        self.photos = []
        self.reply_to_message = None

    async def reply_text(self, text, **kw):
        self.replies.append(text)

    async def reply_photo(self, photo, **kw):
        self.photos.append(photo)


class FakeReplyMsg:
    def __init__(self, data):
        self.photo = [FakePhoto(data)]


class FakeUpdate:
    def __init__(self, uid, photo_data=None):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()
        if photo_data is not None:
            self.message.reply_to_message = FakeReplyMsg(photo_data)


class FakeMe:
    username = "testbot"


class FakeBot:
    def __init__(self):
        self.calls = []

    async def get_me(self):
        return FakeMe()

    async def create_new_sticker_set(self, user_id, name, title, stickers, **kw):
        self.calls.append(("create", user_id, name, title, stickers))
        return True

    async def add_sticker_to_set(self, user_id, name, sticker, **kw):
        self.calls.append(("add", user_id, name, sticker))
        return True


class FakeContext:
    def __init__(self, conn, args=None, bot=None):
        self.args = args or []
        self.bot_data = {"db": conn, "admin_id": None}
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

# 8. /stiker flow: pertama create set, kedua add ke set
conn = db.init_db(":memory:")
bot = FakeBot()
ctx8 = FakeContext(conn, bot=bot)
u1 = FakeUpdate(777, photo)
asyncio.run(meme.stiker_cmd(u1, ctx8))
_t("/stiker pertama: create_new_sticker_set dipanggil",
   len(bot.calls) == 1 and bot.calls[0][0] == "create")
_t("/stiker pertama: nama set serba_777_by_testbot",
   bot.calls[0][2] == "serba_777_by_testbot")
_t("/stiker pertama: emoji default 🙂",
   list(bot.calls[0][4][0].emoji_list) == ["🙂"])
row = conn.execute("SELECT set_name FROM meme_sets WHERE user_id=777").fetchone()
_t("/stiker pertama: set tercatat di DB", row and row[0] == "serba_777_by_testbot")
_t("/stiker pertama: pesan sukses",
   any("Stiker ditambahkan" in r for r in u1.message.replies))
# stiker yang dikirim = PNG max 512 (InputSticker bungkus BytesIO di InputFile)
_raw = bot.calls[0][4][0].sticker.input_file_content
stimg = Image.open(io.BytesIO(_raw))
_t("/stiker pertama: PNG sisi terpanjang 512",
   max(stimg.size) == 512 and stimg.format == "PNG")

u2 = FakeUpdate(777, photo)
asyncio.run(meme.stiker_cmd(u2, ctx8))
_t("/stiker kedua: add_sticker_to_set (bukan create lagi)",
   len(bot.calls) == 2 and bot.calls[1][0] == "add"
   and bot.calls[1][2] == "serba_777_by_testbot")
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

print("MEME " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
