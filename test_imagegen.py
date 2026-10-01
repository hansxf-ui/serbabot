"""Test plugin imagegen: /gambar generate gambar via Pollinations image API.

Semua di-mock (urllib + Telegram + file). Tidak ada network sungguhan.
"""
import asyncio
import glob
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.imagegen as imggen

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
    def __init__(self):
        self.replies = []
        self.edits = []
        self.photos = []
        self.deleted = 0

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self

    async def edit_text(self, text, **kw):
        self.edits.append(text)

    async def reply_photo(self, photo, caption=None, **kw):
        self.photos.append((photo.read(), caption))

    async def delete(self):
        self.deleted += 1


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn, args, admin_id=None):
        self.args = args
        self.bot_data = {"db": conn, "admin_id": admin_id}


# ---------- fake HTTP ----------

FAKE_JPG = b"\xff\xd8\xff" + b"x" * 5000  # >1KB, pura-pura jpeg


class FakeResp:
    def __init__(self, data, status=200):
        self._d = data
        self.status = status

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


http_calls = []


def fake_urlopen(req, timeout=None):
    http_calls.append(req.full_url)
    return FakeResp(FAKE_JPG, 200)


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen

print("== imagegen ==")

conn = db.init_db(":memory:")


def tmp_leftovers():
    return glob.glob(os.path.join("/tmp", "serbaimg_*"))


# 1. sukses: URL benar, foto kekirim, jatah +1, temp dibersihkan
before = set(tmp_leftovers())
upd = FakeUpdate(111)
asyncio.run(imggen.gambar_cmd(upd, FakeContext(conn, ["kucing", "astronot"])))
_t("request ke image.pollinations.ai",
   len(http_calls) == 1 and http_calls[0].startswith(
       "https://image.pollinations.ai/prompt/kucing%20astronot"))
_t("param flux + nologo", "model=flux" in http_calls[0] and "nologo=true" in http_calls[0])
_t("foto kekirim", len(upd.message.photos) == 1
   and upd.message.photos[0][0] == FAKE_JPG)
_t("jatah kepotong 1", db.get_downloads_today(conn, 111) == 1)
_t("temp dibersihkan", set(tmp_leftovers()) == before)
_t("status dihapus", upd.message.deleted == 1)

# 2. tanpa prompt -> cara pakai, tanpa HTTP, jatah aman
http_calls.clear()
upd2 = FakeUpdate(222)
asyncio.run(imggen.gambar_cmd(upd2, FakeContext(conn, [])))
_t("tanpa argumen: kasih cara pakai",
   any("/gambar <deskripsi>" in r for r in upd2.message.replies))
_t("tanpa argumen: tidak panggil HTTP", http_calls == [])
_t("tanpa argumen: jatah tidak kepotong", db.get_downloads_today(conn, 222) == 0)

# 3. jatah habis -> ditolak, HTTP tidak dipanggil
for _ in range(5):
    db.inc_downloads_today(conn, 333)
http_calls.clear()
upd3 = FakeUpdate(333)
asyncio.run(imggen.gambar_cmd(upd3, FakeContext(conn, ["halo"])))
_t("jatah habis: ditolak", any("habis" in r for r in upd3.message.replies))
_t("jatah habis: HTTP tidak dipanggil", http_calls == [])

# 4. timeout -> pesan ramah, jatah tidak kepotong
def _boom(prompt):
    raise TimeoutError("lambat")

imggen._download_image, saved_dl = _boom, imggen._download_image
upd4 = FakeUpdate(444)
asyncio.run(imggen.gambar_cmd(upd4, FakeContext(conn, ["halo"])))
imggen._download_image = saved_dl
_t("timeout: pesan ramah", any("kelamaan" in e for e in upd4.message.edits))
_t("timeout: jatah tidak kepotong", db.get_downloads_today(conn, 444) == 0)
_t("timeout: foto tidak kekirim", upd4.message.photos == [])

# 5. HTTP error -> pesan ramah, jatah tidak kepotong
def fake_500(req, timeout=None):
    return FakeResp(b"err", 500)

urllib.request.urlopen = fake_500
upd5 = FakeUpdate(555)
asyncio.run(imggen.gambar_cmd(upd5, FakeContext(conn, ["halo"])))
urllib.request.urlopen = fake_urlopen
_t("http 500: pesan ramah", any("Gagal" in e for e in upd5.message.edits))
_t("http 500: jatah tidak kepotong", db.get_downloads_today(conn, 555) == 0)

# 6. admin bebas jatah walau quota penuh
for _ in range(5):
    db.inc_downloads_today(conn, 999)
http_calls.clear()
upd6 = FakeUpdate(999)
asyncio.run(imggen.gambar_cmd(upd6, FakeContext(conn, ["halo"], admin_id=999)))
_t("admin: tetap dilayani walau jatah penuh", http_calls != [])
_t("admin: foto sampai", len(upd6.message.photos) == 1)
_t("admin: jatah tidak dicatat", db.get_downloads_today(conn, 999) == 5)

urllib.request.urlopen = real_urlopen

print("IMAGEGEN " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
