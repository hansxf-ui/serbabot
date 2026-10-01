"""Test plugin sholat: parse_timings, next_reminders, /sholat, /sholatset,
/sholatstop, quota.

Semua di-mock (urllib + Telegram + job_queue). Tidak ada network sungguhan.
"""
import asyncio
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.sholat as sholat

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


class FakeJob:
    def __init__(self, name):
        self.name = name
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.dailies = []
        self.once = []

    def get_jobs_by_name(self, name):
        return [j for j in self.dailies + self.once if j.name == name]

    def run_daily(self, cb, time, data, name):
        job = FakeJob(name)
        self.dailies.append(job)
        self.dailies_info = getattr(self, "dailies_info", {})
        self.dailies_info[name] = {"time": time, "data": data}

    def run_once(self, cb, when, data, name):
        job = FakeJob(name)
        self.once.append(job)
        self.once_info = getattr(self, "once_info", {})
        self.once_info[name] = {"when": when, "data": data}


class FakeContext:
    def __init__(self, conn, args, jq=None):
        self.args = args
        self.bot_data = {"db": conn}
        self.job_queue = jq or FakeJobQueue()


# ---------- fake HTTP ----------

SAMPLE = {
    "code": 200,
    "status": "OK",
    "data": {
        "timings": {
            "Fajr": "04:32 (WIB)",
            "Sunrise": "05:49 (WIB)",
            "Dhuhr": "11:52 (WIB)",
            "Asr": "15:14 (WIB)",
            "Maghrib": "17:54 (WIB)",
            "Isha": "19:06 (WIB)",
        },
        "date": {
            "hijri": {
                "day": "09",
                "month": {"en": "Rabi' al-awwal"},
                "year": "1447",
            }
        },
    },
}


class FakeResp:
    def __init__(self, text):
        self._t = text

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


captured = {}
http_calls = []


def fake_urlopen(req, timeout=None):
    http_calls.append(req.full_url)
    captured["url"] = req.full_url
    captured["method"] = req.get_method()
    captured["timeout"] = timeout
    return FakeResp(json.dumps(SAMPLE))


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen

print("== sholat ==")
conn = db.init_db(":memory:")


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, h):
        self.handlers.append(h)


# register() jalan sekali di awal (kayak bot startup): bikin tabel
sholat.register(FakeApp(), conn)

# 1. parse_timings: payload contoh -> 5 waktu + hijri
p = sholat.parse_timings(SAMPLE)
_t("parse: 5 waktu", p["Subuh"] == "04:32" and p["Dzuhur"] == "11:52"
   and p["Ashar"] == "15:14" and p["Maghrib"] == "17:54" and p["Isya"] == "19:06")
_t("parse: suffix (WIB) dibuang", p["Subuh"] == "04:32")
_t("parse: tanggal hijriah", p["hijri"] == "09 Rabi' al-awwal 1447 H")

# 2. parse_timings: key hilang -> '-' dan hijri None, tidak crash
p2 = sholat.parse_timings({"data": {}})
_t("parse kosong: waktu jadi '-'",
   all(p2[n] == "-" for n in ("Subuh", "Dzuhur", "Ashar", "Maghrib", "Isya")))
_t("parse kosong: hijri None", p2["hijri"] is None)

# 3. next_reminders: jam 10:00 -> 4 pengingat (Subuh lewat), 5 menit sebelumnya
now = datetime.datetime(2026, 10, 1, 10, 0)
rems = sholat.next_reminders(p, now=now)
nama_rems = [n for n, _ in rems]
_t("reminders: Subuh lewat di-skip, 4 tersisa",
   nama_rems == ["Dzuhur", "Ashar", "Maghrib", "Isya"])
dz = dict(rems)["Dzuhur"]
_t("reminders: 5 menit sebelum Dzuhur",
   dz == datetime.datetime(2026, 10, 1, 11, 47))

# 4. next_reminders: semua lewat -> kosong
rems4 = sholat.next_reminders(p, now=datetime.datetime(2026, 10, 1, 23, 0))
_t("reminders: malam hari = kosong", rems4 == [])

# 5. next_reminders: tengah malam -> 5 pengingat
rems5 = sholat.next_reminders(p, now=datetime.datetime(2026, 10, 1, 0, 1))
_t("reminders: 00:01 = 5 pengingat", len(rems5) == 5)

# 6. /sholat sukses: URL benar (method=20 KEMENAG), reply ada 5 waktu
upd = FakeUpdate(111)
asyncio.run(sholat.sholat_cmd(upd, FakeContext(conn, ["Jakarta"])))
_t("request ke api.aladhan.com", "api.aladhan.com/v1/timingsByCity" in captured.get("url", ""))
_t("method=20 (KEMENAG)", "method=20" in captured.get("url", ""))
_t("country=Indonesia", "country=Indonesia" in captured.get("url", ""))
_t("timeout 15s", captured.get("timeout") == 15)
_t("kota URL-encode aman", "city=Jakarta" in captured.get("url", ""))
e = upd.message.edits[0] if upd.message.edits else ""
_t("reply: kota + 5 waktu",
   "Jakarta" in e and all(x in e for x in ("Subuh", "Dzuhur", "Ashar", "Maghrib", "Isya")))
_t("reply: jam kebaca", "04:32" in e and "19:06" in e)
_t("reply: hijriah kebaca", "1447" in e)
_t("sukses: quota kepotong 1", db.get_quota_today(conn, "sholat", 111) == 1)

# 7. /sholat: kota 2 kata di-encode
http_calls.clear()
upd7 = FakeUpdate(112)
asyncio.run(sholat.sholat_cmd(upd7, FakeContext(conn, ["Banda", "Aceh"])))
_t("kota 2 kata: di-encode", "city=Banda+Aceh" in captured.get("url", ""))

# 8. /sholat: API mati -> pesan ramah, quota aman
def fake_down(req, timeout=None):
    raise OSError("connection refused")

urllib.request.urlopen = fake_down
upd8 = FakeUpdate(113)
asyncio.run(sholat.sholat_cmd(upd8, FakeContext(conn, ["Jakarta"])))
urllib.request.urlopen = fake_urlopen
_t("API mati: pesan ramah",
   any("ambil jadwal" in x for x in upd8.message.edits))
_t("API mati: quota tidak kepotong",
   db.get_quota_today(conn, "sholat", 113) == 0)

# 9. /sholat tanpa kota -> cara pakai
upd9 = FakeUpdate(114)
asyncio.run(sholat.sholat_cmd(upd9, FakeContext(conn, [])))
_t("tanpa kota: kasih cara pakai",
   any("/sholat <kota>" in x for x in upd9.message.replies))

# 10. Quota 20/hari: penuh -> ditolak
for _ in range(20):
    db.inc_quota_today(conn, "sholat", 321)
http_calls.clear()
upd10 = FakeUpdate(321)
asyncio.run(sholat.sholat_cmd(upd10, FakeContext(conn, ["Jakarta"])))
_t("quota habis: ditolak", any("habis" in x for x in upd10.message.replies))
_t("quota habis: HTTP tidak dipanggil", http_calls == [])

# 11. VIP: quota penuh tetap dilayani
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(666,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
for _ in range(20):
    db.inc_quota_today(conn, "sholat", 666)
http_calls.clear()
upd11 = FakeUpdate(666)
asyncio.run(sholat.sholat_cmd(upd11, FakeContext(conn, ["Jakarta"])))
_t("vip: tetap dilayani walau quota penuh", http_calls != [])
_t("vip: quota tidak dicatat", db.get_quota_today(conn, "sholat", 666) == 20)

# 12. /sholatset: simpan ke DB + jadwalkan job harian 00:05
jq = FakeJobQueue()
upd12 = FakeUpdate(222)
asyncio.run(sholat.sholatset_cmd(upd12, FakeContext(conn, ["Bandung"], jq)))
row = conn.execute("SELECT city FROM sholat_subs WHERE user_id=222").fetchone()
_t("sholatset: kota tersimpan", row and row[0] == "Bandung")
info = jq.dailies_info.get("sholat_daily_222")
_t("sholatset: run_daily dijadwalkan",
   info is not None and info["data"] == {"user_id": 222, "city": "Bandung"})
_t("sholatset: jam 00:05 WIB",
   info is not None and info["time"].hour == 0 and info["time"].minute == 5)
_t("sholatset: reply konfirmasi",
   any("Bandung" in x for x in upd12.message.replies))

# 13. /sholatset 2x: kota di-update, job lama di-remove
old = [j for j in jq.dailies if j.name == "sholat_daily_222"][0]
upd13 = FakeUpdate(222)
asyncio.run(sholat.sholatset_cmd(upd13, FakeContext(conn, ["Surabaya"], jq)))
row13 = conn.execute("SELECT city FROM sholat_subs WHERE user_id=222").fetchone()
_t("sholatset ulang: kota di-update", row13 and row13[0] == "Surabaya")
_t("sholatset ulang: job lama di-remove", old.removed)

# 14. /sholatset tanpa kota -> cara pakai
upd14 = FakeUpdate(223)
asyncio.run(sholat.sholatset_cmd(upd14, FakeContext(conn, [], jq)))
_t("sholatset tanpa kota: kasih cara pakai",
   any("/sholatset <kota>" in x for x in upd14.message.replies))

# 15. /sholatstop: hapus langganan + job
upd15 = FakeUpdate(222)
asyncio.run(sholat.sholatstop_cmd(upd15, FakeContext(conn, [], jq)))
row15 = conn.execute("SELECT city FROM sholat_subs WHERE user_id=222").fetchone()
_t("sholatstop: langganan dihapus", row15 is None)
_t("sholatstop: job di-remove",
   all(j.removed for j in jq.get_jobs_by_name("sholat_daily_222")))
_t("sholatstop: reply konfirmasi",
   any("dimatiin" in x for x in upd15.message.replies))

# 16. register(): daftarkan 3 handler (tabel dibuat di awal, lihat atas)
app = FakeApp()
sholat.register(app, conn)
_t("register: tabel sholat_subs ada",
   conn.execute("SELECT name FROM sqlite_master WHERE name='sholat_subs'").fetchone()
   is not None)
_t("register: 3 handler", len(app.handlers) == 3)

# 17. register() idempoten: dipanggil 2x tidak error (IF NOT EXISTS)
sholat.register(app, conn)
_t("register: idempoten", True)

urllib.request.urlopen = real_urlopen

print("SHOLAT " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
