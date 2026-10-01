"""Test plugin primbon: weton, neptu, zodiak, jodoh, quota.

Semua di-mock (Telegram). Murni Python — tanpa network.
"""
import asyncio
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.primbon as primbon

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

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self

    async def edit_text(self, text, **kw):
        self.edits.append(text)


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn, args):
        self.args = args
        self.bot_data = {"db": conn}


print("== primbon ==")
conn = db.init_db(":memory:")

# 1. Anchor: 17-08-1945 = Jumat Legi
_t("17-08-1945 = Jumat Legi",
   primbon.weton_of(primbon._parse_tgl("17-08-1945")) == ("Jumat", "Legi"))

# 2. Neptu Senin Legi = 9
_t("neptu Senin Legi = 9", primbon.neptu("Senin", "Legi") == 9)
_t("neptu Jumat Legi = 11", primbon.neptu("Jumat", "Legi") == 11)

# 3. /weton command: anchor beneran + neptu + watak, quota kepotong
upd = FakeUpdate(111)
asyncio.run(primbon.weton_cmd(upd, FakeContext(conn, ["17-08-1945"])))
r = upd.message.replies[0] if upd.message.replies else ""
_t("/weton: Jumat Legi", "Jumat Legi" in r)
_t("/weton: neptu 11 (6+5)", "Neptu: 11 (6+5)" in r)
_t("/weton: ada watak", "Watak:" in r)
_t("/weton: quota kepotong 1", db.get_quota_today(conn, "primbon", 111) == 1)

# 4. Zodiak: batas 19 Feb vs 20 Feb beda
_t("19 Feb = Aquarius", primbon.zodiak_of(19, 2)[0] == "Aquarius")
_t("20 Feb = Pisces", primbon.zodiak_of(20, 2)[0] == "Pisces")
_t("1 Jan = Capricorn", primbon.zodiak_of(1, 1)[0] == "Capricorn")
_t("31 Des = Capricorn", primbon.zodiak_of(31, 12)[0] == "Capricorn")

# 5. /zodiak command: nama + ramalan, quota kepotong
upd5 = FakeUpdate(555)
asyncio.run(primbon.zodiak_cmd(upd5, FakeContext(conn, ["20-02-2000"])))
r5 = upd5.message.replies[0] if upd5.message.replies else ""
_t("/zodiak: Pisces", "Pisces" in r5)
_t("/zodiak: ada aspek hari ini",
   any(a in r5 for a in ("Asmara", "Karier", "Kesehatan")))
_t("/zodiak: quota kepotong 1", db.get_quota_today(conn, "primbon", 555) == 1)

# 6. Ramalan: varian ganti tiap hari, deterministik
a1, t1 = primbon.ramalan("Pisces", 5)
a2, t2 = primbon.ramalan("Pisces", 6)
_t("ramalan: beda hari beda varian", (a1, t1) != (a2, t2))
_t("ramalan: input sama = output sama",
   primbon.ramalan("Pisces", 5) == primbon.ramalan("Pisces", 5))
_t("ramalan: 12 zodiak x 3 template terisi",
   all(len(v) == 3 and all(x.strip() for x in v)
       for v in primbon.RAMALAN.values())
   and len(primbon.RAMALAN) == 12)

# 7. /jodoh deterministik: 2x panggil hasil sama persis
def _jodoh_reply(uid):
    u = FakeUpdate(uid)
    asyncio.run(primbon.jodoh_cmd(u, FakeContext(conn, ["17-08-1945", "20-05-1990"])))
    return u.message.replies

_t("/jodoh deterministik", _jodoh_reply(777) == _jodoh_reply(778))
rj = _jodoh_reply(779)[0]
_t("/jodoh: ada total neptu", "Total neptu:" in rj)
_t("/jodoh: ada kategori valid",
   any(k in rj for k in primbon.JODOH_KATEGORI))
_t("/jodoh: ada disclaimer", "jangan dibawa serius" in rj)
_t("/jodoh: quota kepotong", db.get_quota_today(conn, "primbon", 779) == 1)
_t("jodoh_kategori: total % 5",
   primbon.jodoh_kategori(28)[0] == primbon.JODOH_KATEGORI[28 % 5])

# 8. Format tanggal salah -> contoh pakai, quota aman
upd8 = FakeUpdate(888)
asyncio.run(primbon.zodiak_cmd(upd8, FakeContext(conn, ["17/08/1945"])))
_t("format salah: kasih contoh", any("/zodiak 17-08-1945" in x
                                     for x in upd8.message.replies))
_t("format salah: quota tidak kepotong",
   db.get_quota_today(conn, "primbon", 888) == 0)

# 9. Tanggal nggak valid (31 Feb) -> ditolak
upd9 = FakeUpdate(889)
asyncio.run(primbon.weton_cmd(upd9, FakeContext(conn, ["31-02-2000"])))
_t("tanggal invalid: ditolak dengan contoh",
   any("17-08-1945" in x for x in upd9.message.replies))

# 10. Tanpa argumen -> cara pakai
upd10 = FakeUpdate(890)
asyncio.run(primbon.jodoh_cmd(upd10, FakeContext(conn, ["17-08-1945"])))
_t("argumen kurang: kasih cara pakai",
   any("/jodoh <tgl1> <tgl2>" in x for x in upd10.message.replies))

# 11. Quota 30/hari: penuh -> ditolak
for _ in range(30):
    db.inc_quota_today(conn, "primbon", 321)
upd11 = FakeUpdate(321)
asyncio.run(primbon.zodiak_cmd(upd11, FakeContext(conn, ["20-02-2000"])))
_t("quota habis: ditolak", any("habis" in x for x in upd11.message.replies))

# 12. Premium/VIP: quota penuh tetap dilayani
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(666,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
for _ in range(30):
    db.inc_quota_today(conn, "primbon", 666)
upd12 = FakeUpdate(666)
asyncio.run(primbon.weton_cmd(upd12, FakeContext(conn, ["17-08-1945"])))
_t("vip: tetap dilayani walau quota penuh",
   any("Jumat Legi" in x for x in upd12.message.replies))

# 13. Watak: gabungan 2 kalimat (hari + pasaran)
w = primbon.watak("Senin", "Legi")
_t("watak: gabungan hari + pasaran",
   primbon.WATAK_HARI["Senin"] in w and primbon.WATAK_PASARAN["Legi"] in w)

# 14. Weton konsisten maju 1 hari dari anchor
kemarin = primbon.weton_of(datetime.date(1945, 8, 18))
_t("18-08-1945 = Sabtu Pahing", kemarin == ("Sabtu", "Pahing"))

print("PRIMBON " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
