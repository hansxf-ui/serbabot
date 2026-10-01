"""Test plugin gacha: rarity, gambar kartu, duplikat->koin, limit harian.

Semua di-mock (Telegram). Tidak ada network sungguhan.
"""
import asyncio
import io
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from PIL import Image

import plugins.gacha as gacha

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
        self.first_name = "Tester"


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.photos = []
        self.reply_to_message = None

    async def reply_text(self, text, **kw):
        self.replies.append(text)

    async def reply_photo(self, photo, caption=None, **kw):
        self.photos.append((photo, caption))


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn, args=None):
        self.args = args or []
        self.bot_data = {"db": conn, "admin_id": None}


print("== gacha ==")

# 1. cards.json: minimal 30 kartu, format lengkap, semua rarity terwakili
_t("minimal 30 kartu", len(gacha.CARDS) >= 30)
_needed = {"id", "name", "title", "rarity", "desc"}
_t("semua kartu punya field lengkap",
   all(_needed <= set(c.keys()) for c in gacha.CARDS))
_t("rarity valid semua",
   all(c["rarity"] in ("common", "rare", "epic", "legendary")
       for c in gacha.CARDS))
for r in ("common", "rare", "epic", "legendary"):
    _t("rarity %s terwakili" % r,
       any(c["rarity"] == r for c in gacha.CARDS))
_t("id unik semua", len({c["id"] for c in gacha.CARDS}) == len(gacha.CARDS))

# 2. pick_rarity deterministik via injeksi roll
_t("roll 0.00 -> common", gacha.pick_rarity(lambda: 0.00) == "common")
_t("roll 0.599 -> common", gacha.pick_rarity(lambda: 0.599) == "common")
_t("roll 0.60 -> rare", gacha.pick_rarity(lambda: 0.60) == "rare")
_t("roll 0.849 -> rare", gacha.pick_rarity(lambda: 0.849) == "rare")
_t("roll 0.85 -> epic", gacha.pick_rarity(lambda: 0.85) == "epic")
_t("roll 0.969 -> epic", gacha.pick_rarity(lambda: 0.969) == "epic")
_t("roll 0.97 -> legendary", gacha.pick_rarity(lambda: 0.97) == "legendary")
_t("roll 0.999 -> legendary", gacha.pick_rarity(lambda: 0.999) == "legendary")
_t("roll float langsung 0.5 -> common", gacha.pick_rarity(0.5) == "common")

# 3. distribusi kasar sesuai probabilitas (seed tetap -> deterministik)
rng = random.Random(20261001)
N = 20000
cnt = {"common": 0, "rare": 0, "epic": 0, "legendary": 0}
for _ in range(N):
    cnt[gacha.pick_rarity(rng.random)] += 1
p = {k: v / N for k, v in cnt.items()}
_t("common ~60%% (dapat %.1f%%)" % (p["common"] * 100), 0.55 < p["common"] < 0.65)
_t("rare ~25%% (dapat %.1f%%)" % (p["rare"] * 100), 0.22 < p["rare"] < 0.28)
_t("epic ~12%% (dapat %.1f%%)" % (p["epic"] * 100), 0.10 < p["epic"] < 0.14)
_t("legendary ~3%% (dapat %.1f%%)" % (p["legendary"] * 100),
   0.015 < p["legendary"] < 0.045)

# 4. draw_card: PNG valid 512x768 untuk tiap rarity
one_of = {}
for c in gacha.CARDS:
    one_of.setdefault(c["rarity"], c)
for r, card in one_of.items():
    buf = gacha.draw_card(card)
    im = Image.open(buf)
    _t("draw_card %s: PNG 512x768" % r,
       im.format == "PNG" and im.size == (512, 768))
    buf.seek(0)
    _t("draw_card %s: bisa dibaca ulang" % r, len(buf.read()) > 1000)

# 5. duplikat -> koin sesuai tabel rarity
for i, r in enumerate(("common", "rare", "epic", "legendary")):
    conn = db.init_db(":memory:")
    uid = 5000 + i
    card = one_of[r]
    b1 = gacha._give_card(conn, uid, card)
    b2 = gacha._give_card(conn, uid, card)
    n = conn.execute(
        "SELECT count FROM gacha_collection WHERE user_id=? AND card_id=?",
        (uid, card["id"])).fetchone()[0]
    _t("duplikat %s: pertama tanpa bonus, kedua +%d koin" % (r, gacha.DUPE_COINS[r]),
       b1 == 0 and b2 == gacha.DUPE_COINS[r]
       and db.get_coins(conn, uid) == gacha.DUPE_COINS[r] and n == 2)

# 6. /gacha gratis 1x/hari, yang kedua ditolak
conn = db.init_db(":memory:")
upd = FakeUpdate(111)
asyncio.run(gacha.gacha_cmd(upd, FakeContext(conn)))
_t("/gacha pertama: kartu dikirim", len(upd.message.photos) == 1)
_t("/gacha pertama: caption ada nama kartu",
   upd.message.photos[0][1] is not None and len(upd.message.photos[0][1]) > 10)
upd2 = FakeUpdate(111)
asyncio.run(gacha.gacha_cmd(upd2, FakeContext(conn)))
_t("/gacha kedua hari yang sama: ditolak",
   any("udah kepake" in r for r in upd2.message.replies))
_t("/gacha kedua: tidak kirim foto lagi", upd2.message.photos == [])
pulls = conn.execute(
    "SELECT count FROM gacha_pulls WHERE user_id=?", (111,)).fetchone()[0]
_t("pull gratis tercatat 1x", pulls == 1)

# 7. /gachaplus: potong 50 koin
conn = db.init_db(":memory:")
db.add_coins(conn, 222, 100, "test")
upd3 = FakeUpdate(222)
asyncio.run(gacha.gachaplus_cmd(upd3, FakeContext(conn)))
_t("/gachaplus: kartu dikirim", len(upd3.message.photos) == 1)
_t("/gachaplus: saldo 100 -> 50", db.get_coins(conn, 222) == 50)

# 8. /gachaplus tanpa koin: ditolak, saldo aman
conn = db.init_db(":memory:")
upd4 = FakeUpdate(333)
asyncio.run(gacha.gachaplus_cmd(upd4, FakeContext(conn)))
_t("/gachaplus koin kurang: ditolak",
   any("kurang" in r and "/checkin" in r for r in upd4.message.replies))
_t("/gachaplus koin kurang: tidak kirim foto", upd4.message.photos == [])
_t("/gachaplus koin kurang: koleksi kosong",
   conn.execute("SELECT count(*) FROM gacha_collection").fetchone()[0] == 0)

# 9. /koleksi: daftar + total unik
conn = db.init_db(":memory:")
db.upsert_user(conn, 444, "tester")
asyncio.run(gacha.gacha_cmd(FakeUpdate(444), FakeContext(conn)))
asyncio.run(gacha.gacha_cmd(FakeUpdate(444), FakeContext(conn)))  # ditolak (gratis habis)
db.add_coins(conn, 444, 200, "test")
asyncio.run(gacha.gachaplus_cmd(FakeUpdate(444), FakeContext(conn)))
updk = FakeUpdate(444)
asyncio.run(gacha.koleksi_cmd(updk, FakeContext(conn)))
unik = conn.execute(
    "SELECT count(*) FROM gacha_collection WHERE user_id=?", (444,)).fetchone()[0]
_t("/koleksi: tampilkan total unik",
   any(str(unik) in r and "unik" in r for r in updk.message.replies))
_t("/koleksi: ada nama kartu di daftar",
   any(any(c["name"] in r for c in gacha.CARDS) for r in updk.message.replies))

# 10. /koleksi kosong: pesan ramah
conn = db.init_db(":memory:")
upde = FakeUpdate(555)
asyncio.run(gacha.koleksi_cmd(upde, FakeContext(conn)))
_t("/koleksi kosong: ajak /gacha",
   any("/gacha" in r for r in upde.message.replies))

print("GACHA " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
