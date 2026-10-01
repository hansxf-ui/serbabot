"""Test plugin fotofun: template valid, foto tidak gepeng, koin, template invalid.

Semua di-mock (PIL asli, Telegram palsu). Tidak ada network.
"""
import asyncio
import os
import sys
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.fotofun as fotofun
from PIL import Image

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


def _src_photo(w=300, h=500, color=(30, 120, 200)):
    img = Image.new("RGB", (w, h), color)
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ---------- fake Telegram ----------

class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester"


class FakeChat:
    def __init__(self, cid):
        self.id = cid


class FakePhotoSize:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.photos = []
        self.reply_to_message = None
        self.chat = FakeChat(99)

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        if "reply_markup" in kw:
            self.markup = kw["reply_markup"]

    async def reply_photo(self, photo, caption=None, **kw):
        self.photos.append((photo, caption))


class FakeCallbackQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answered = False

    async def answer(self):
        self.answered = True


class FakeFile:
    def __init__(self, data):
        self._d = data

    async def download_as_bytearray(self):
        return bytearray(self._d)


class FakeBot:
    def __init__(self, photo_bytes):
        self._pb = photo_bytes
        self.got_file = []

    async def get_file(self, file_id):
        self.got_file.append(file_id)
        return FakeFile(self._pb)


class FakeContext:
    def __init__(self, conn, bot):
        self.bot = bot
        self.bot_data = {"db": conn}


class FakeUpdate:
    def __init__(self, uid, callback=None, message=None):
        self.effective_user = FakeUser(uid)
        self.callback_query = callback
        self.message = message


print("== fotofun ==")

# 1. apply_template: 3 template -> PNG valid, ukuran kanvas
for tpl in ("wanted", "breaking", "piala"):
    out = fotofun.apply_template(_src_photo(), tpl)
    raw = out.getvalue()
    ok_png = raw[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(BytesIO(raw))
    _t(f"template {tpl}: PNG valid", ok_png and img.format == "PNG")
    _t(f"template {tpl}: ukuran kanvas {fotofun.CANVAS}", img.size == fotofun.CANVAS)
    # box foto di output berdimensi sesuai BOX (tidak gepeng)
    x, y, bw, bh = fotofun.BOX[tpl]
    region = img.crop((x, y, x + bw, y + bh))
    _t(f"template {tpl}: box foto {bw}x{bh}", region.size == (bw, bh))

# 2. foto tidak gepeng: garis vertikal 10px di sumber 100x50
#    -> cover-crop ke 200x200 harus jadi garis 40px (scale=4 utuh)
strip = Image.new("RGB", (100, 50))
spx = strip.load()
for sx in range(100):
    for sy in range(50):
        spx[sx, sy] = (255, 0, 0) if (sx // 10) % 2 == 0 else (0, 0, 255)
cropped = fotofun._cover_crop(strip, 200, 200)
_t("cover-crop: ukuran box pas", cropped.size == (200, 200))
p10 = cropped.getpixel((10, 100))
p30 = cropped.getpixel((30, 100))
p70 = cropped.getpixel((70, 100))
p110 = cropped.getpixel((110, 100))
red = lambda p: p[0] > 200 and p[2] < 100
blue = lambda p: p[2] > 200 and p[0] < 100
# crop mulai di x=100 (bukan kelipatan 40): pola yg benar = merah,biru,merah,biru.
# Kalau di-gepeng (resize langsung), garisnya 20px -> (70,100) jadi biru.
_t("cover-crop: garis tidak gepeng (40px, bukan 20px)",
   red(p10) and blue(p30) and red(p70) and blue(p110))

# 3. koin kurang -> ditolak, saldo tidak kepotong
conn = db.init_db(":memory:")
bot = FakeBot(_src_photo())
ctx = FakeContext(conn, bot)
ctx.bot_data["memein:111"] = "file_abc"
db.upsert_user(conn, 111, "tester")
msg = FakeMessage()
upd = FakeUpdate(111, callback=FakeCallbackQuery("memein:wanted", msg))
asyncio.run(fotofun.memein_pick(upd, ctx))
_t("koin 0: ditolak", any("koin kurang (20)" in r for r in msg.replies))
_t("koin 0: ada saran /checkin", any("/checkin" in r for r in msg.replies))
_t("koin 0: saldo tetap 0", db.get_coins(conn, 111) == 0)
_t("koin 0: foto tidak dikirim", msg.photos == [])

# 4. koin cukup -> kepotong 20, foto kekirim
db.add_coins(conn, 222, 50, "test")
ctx.bot_data["memein:222"] = "file_def"
msg2 = FakeMessage()
upd2 = FakeUpdate(222, callback=FakeCallbackQuery("memein:piala", msg2))
asyncio.run(fotofun.memein_pick(upd2, ctx))
_t("koin cukup: kepotong 20", db.get_coins(conn, 222) == 30)
_t("koin cukup: foto kekirim", len(msg2.photos) == 1)
raw2 = msg2.photos[0][0].getvalue()
_t("koin cukup: hasil PNG valid", raw2[:8] == b"\x89PNG\r\n\x1a\n")

# 5. VIP tetap bayar koin (koin sink, bukan quota)
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(333,'vip',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
db.add_coins(conn, 333, 100, "test")
ctx.bot_data["memein:333"] = "file_ghi"
msg3 = FakeMessage()
upd3 = FakeUpdate(333, callback=FakeCallbackQuery("memein:breaking", msg3))
asyncio.run(fotofun.memein_pick(upd3, ctx))
_t("VIP: tetap bayar 20 koin", db.get_coins(conn, 333) == 80)
_t("VIP: foto kekirim", len(msg3.photos) == 1)

# 6. template_id invalid -> ditolak
try:
    fotofun.apply_template(_src_photo(), "ngawur")
    _t("template invalid: apply_template raise ValueError", False)
except ValueError:
    _t("template invalid: apply_template raise ValueError", True)
msg4 = FakeMessage()
upd4 = FakeUpdate(444, callback=FakeCallbackQuery("memein:ngawur", msg4))
asyncio.run(fotofun.memein_pick(upd4, ctx))
_t("template invalid: callback ditolak ramah",
   any("nggak dikenal" in r for r in msg4.replies))
_t("template invalid: koin tidak kepotong", db.get_coins(conn, 444) == 0)

# 7. /memein tanpa reply foto -> diminta reply dulu
msg5 = FakeMessage()
msg5.reply_to_message = None
upd5 = FakeUpdate(555, message=msg5)
asyncio.run(fotofun.memein_cmd(upd5, ctx))
_t("/memein tanpa reply: diminta reply foto",
   any("Reply ke foto" in r for r in msg5.replies))

# 8. /memein reply foto -> keyboard 3 template
msg6 = FakeMessage()
rm = FakeMessage()
rm.photo = [FakePhotoSize("small"), FakePhotoSize("file_big")]
msg6.reply_to_message = rm
upd6 = FakeUpdate(666, message=msg6)
asyncio.run(fotofun.memein_cmd(upd6, ctx))
btns = [b.text for row in msg6.markup.inline_keyboard for b in row]
_t("/memein reply foto: 3 tombol template", len(btns) == 3)
_t("/memein reply foto: file_id terbesar disimpan",
   ctx.bot_data.get("memein:666") == "file_big")

print("FOTOFUN " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
