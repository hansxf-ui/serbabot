"""Test plugin skor: tebak skor bola pakai koin virtual.

Semua di-mock (urllib + Telegram). Tidak ada network sungguhan.
"""
import asyncio
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.skor as skor

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------- fake Telegram ----------

class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "tester%d" % uid


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn, args, admin_id=1):
        self.args = args
        self.bot_data = {"db": conn, "admin_id": admin_id}


def fresh_conn():
    conn = db.init_db(":memory:")
    conn.executescript(skor.SCHEMA)
    return conn


print("== skor ==")

# 1. /skor kosong
conn = fresh_conn()
upd = FakeUpdate(2)
asyncio.run(skor.skor_cmd(upd, FakeContext(conn, [])))
_t("/skor kosong: ajak admin /addmatch",
   any("/addmatch" in r for r in upd.message.replies))
_t("/skor kosong: ada disclaimer bukan judi",
   any("BUKAN judi" in r for r in upd.message.replies))

# 2. /addmatch non-admin ditolak
upd2 = FakeUpdate(2)
asyncio.run(skor.addmatch_cmd(upd2, FakeContext(conn,
    ["Indonesia", "vs", "Malaysia", "2026-10-05", "19:00"])))
_t("/addmatch non-admin ditolak",
   any("cuma admin" in r for r in upd2.message.replies))
_t("/addmatch non-admin: match tidak masuk",
   skor.get_open_matches(conn) == [])

# 3. /addmatch admin: parse OK
upd3 = FakeUpdate(1)
asyncio.run(skor.addmatch_cmd(upd3, FakeContext(conn,
    ["Indonesia", "vs", "Malaysia", "2026-10-05", "19:00"])))
_t("/addmatch admin: balasan OK", any("#1" in r for r in upd3.message.replies))
open_ = skor.get_open_matches(conn)
_t("/addmatch admin: 1 match open",
   len(open_) == 1 and open_[0][1] == "Indonesia" and open_[0][2] == "Malaysia"
   and open_[0][3] == "2026-10-05 19:00")

# 4. /addmatch admin: format salah
upd3b = FakeUpdate(1)
asyncio.run(skor.addmatch_cmd(upd3b, FakeContext(conn, ["ngawur"])))
_t("/addmatch admin: format salah kasih cara pakai",
   any("vs" in r for r in upd3b.message.replies))

# 5. spend gagal (koin kurang) — user 0 koin
db.upsert_user(conn, 2, "tester2")
upd5 = FakeUpdate(2)
asyncio.run(skor.tebak_cmd(upd5, FakeContext(conn, ["1", "2-1"])))
_t("koin kurang: ditolak", any("Koin lu kurang" in r for r in upd5.message.replies))
_t("koin kurang: /checkin disarankan",
   any("/checkin" in r for r in upd5.message.replies))
_t("koin kurang: tebakan tidak tercatat",
   conn.execute("SELECT COUNT(*) FROM skor_tebak").fetchone()[0] == 0)

# 6. format skor salah
db.add_coins(conn, 2, 100, "modal test")
upd6 = FakeUpdate(2)
asyncio.run(skor.tebak_cmd(upd6, FakeContext(conn, ["1", "dua-satu"])))
_t("format skor salah: ditolak",
   any("Format skornya salah" in r for r in upd6.message.replies))
_t("format skor salah: koin tidak kepotong", db.get_coins(conn, 2) == 100)

# 7. tebak sukses: 10 koin kepotong, tercatat
upd7 = FakeUpdate(2)
asyncio.run(skor.tebak_cmd(upd7, FakeContext(conn, ["1", "2-1"])))
_t("tebak sukses: konfirmasi", any("Tebakan masuk" in r for r in upd7.message.replies))
_t("tebak sukses: 10 koin kepotong", db.get_coins(conn, 2) == 90)
_t("tebak sukses: prediksi tercatat",
   conn.execute("SELECT home_pred, away_pred FROM skor_tebak"
                " WHERE match_id=1 AND user_id=2").fetchone() == (2, 1))

# 8. tebak ganda ditolak, koin aman
upd8 = FakeUpdate(2)
asyncio.run(skor.tebak_cmd(upd8, FakeContext(conn, ["1", "3-0"])))
_t("tebak ganda: ditolak",
   any("Satu aja per match" in r for r in upd8.message.replies))
_t("tebak ganda: koin tidak kepotong lagi", db.get_coins(conn, 2) == 90)

# 9. /tebak match nggak ada
upd9 = FakeUpdate(2)
asyncio.run(skor.tebak_cmd(upd9, FakeContext(conn, ["99", "1-0"])))
_t("match nggak ada: ditolak", any("nggak ada" in r for r in upd9.message.replies))

# 10. /setresult non-admin ditolak
upd10 = FakeUpdate(2)
asyncio.run(skor.setresult_cmd(upd10, FakeContext(conn, ["1", "2-1"])))
_t("/setresult non-admin ditolak",
   any("cuma admin" in r for r in upd10.message.replies))
_t("/setresult non-admin: status tetap open",
   skor.get_match(conn, 1)[4] == "open")

# 11. /setresult admin: yang tepat +50, yang salah tidak
db.upsert_user(conn, 3, "tester3")
db.add_coins(conn, 3, 100, "modal test")
upd11a = FakeUpdate(3)
asyncio.run(skor.tebak_cmd(upd11a, FakeContext(conn, ["1", "0-0"])))  # salah
_t("user3 tebak salah masuk", db.get_coins(conn, 3) == 90)
upd11 = FakeUpdate(1)
asyncio.run(skor.setresult_cmd(upd11, FakeContext(conn, ["1", "2-1"])))
_t("/setresult admin: balasan pemenang",
   any("1 pemenang" in r for r in upd11.message.replies))
_t("settle: user2 tepat -> +50 (90+50=140)", db.get_coins(conn, 2) == 140)
_t("settle: user3 salah -> tetap 90", db.get_coins(conn, 3) == 90)
_t("settle: status done", skor.get_match(conn, 1)[4] == "done")
_t("settle: kolom won=1 cuma buat yang tepat",
   conn.execute("SELECT user_id, won FROM skor_tebak ORDER BY user_id")
   .fetchall() == [(2, 1), (3, 0)])

# 12. settle idempoten: dipanggil lagi tidak bagi ulang
w2 = skor.settle_match(conn, 1)
_t("settle ulang: tidak ada pemenang baru", w2 == [])
_t("settle ulang: saldo tidak berubah", db.get_coins(conn, 2) == 140)

# 13. leaderboard won
conn.execute("INSERT INTO users(user_id, username, created_at)"
             " VALUES(2,'tester2','x') ON CONFLICT(user_id)"
             " DO UPDATE SET username=excluded.username")
conn.commit()
lb = skor.get_leaderboard(conn)
_t("leaderboard: 1 entri, user2 menang 1x",
   lb == [(2, "tester2", 1)])

# 14. /skor tampilkan leaderboard
upd14 = FakeUpdate(2)
asyncio.run(skor.skor_cmd(upd14, FakeContext(conn, [])))
_t("/skor: leaderboard tampil",
   any("tester2" in r and "1x tepat" in r for r in upd14.message.replies))

# 15. fetch_finished di-mock: URL, header, timeout, parse
captured = {}


class FakeResp:
    def __init__(self, text):
        self._t = text

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


FD_JSON = json.dumps({"matches": [
    {"homeTeam": {"shortName": "Indonesia"}, "awayTeam": {"shortName": "Malaysia"},
     "score": {"fullTime": {"home": 2, "away": 1}}},
    {"homeTeam": {"name": "Tim X"}, "awayTeam": {"name": "Tim Y"},
     "score": {"fullTime": {"home": 0, "away": 0}}},
    {"homeTeam": {"shortName": "Belum"}, "awayTeam": {"shortName": "Main"},
     "score": {"fullTime": {"home": None, "away": None}}},
]})


def fake_urlopen(req, timeout=None):
    captured["url"] = req.full_url
    captured["token"] = req.get_header("X-auth-token")  # add_header meng-capitalize key
    captured["timeout"] = timeout
    return FakeResp(FD_JSON)


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
res = skor.fetch_finished("KUNCI123")
urllib.request.urlopen = real_urlopen
_t("fetch: URL status=FINISHED", captured.get("url") == skor.API_URL)
_t("fetch: header X-Auth-Token", captured.get("token") == "KUNCI123")
_t("fetch: timeout 15", captured.get("timeout") == 15)
_t("fetch: parse bener, skor null di-skip",
   res == [("Indonesia", "Malaysia", 2, 1), ("Tim X", "Tim Y", 0, 0)])

# 16. auto_settle: cocok case-insensitive contains + kickoff lewat
conn2 = fresh_conn()
mid2 = skor.add_match(conn2, "indonesia", "malaysia", "2026-09-01 19:00")
db.upsert_user(conn2, 7, "u7")
db.add_coins(conn2, 7, 50, "modal")
ok_bet, _ = skor.place_bet(conn2, 7, mid2, 2, 1)
_t("setup auto_settle: bet masuk", ok_bet)
urllib.request.urlopen = fake_urlopen
settled = skor.auto_settle(conn2, "KUNCI123",
                            now=datetime.datetime(2026, 10, 1, 20, 0, 0))
urllib.request.urlopen = real_urlopen
_t("auto_settle: match ke-settle", settled == [mid2])
_t("auto_settle: skor tercatat",
   skor.get_match(conn2, mid2)[5:] == (2, 1))
_t("auto_settle: pemenang +50 (40+50=90)", db.get_coins(conn2, 7) == 90)

# 17. auto_settle: nama nggak cocok -> skip
conn3 = fresh_conn()
mid3 = skor.add_match(conn3, "Brazil", "Argentina", "2026-09-01 19:00")
urllib.request.urlopen = fake_urlopen
settled3 = skor.auto_settle(conn3, "KUNCI123",
                            now=datetime.datetime(2026, 10, 1, 20, 0, 0))
urllib.request.urlopen = real_urlopen
_t("auto_settle: nama tak cocok di-skip", settled3 == [])
_t("auto_settle: status tetap open", skor.get_match(conn3, mid3)[4] == "open")

# 18. register: tanpa FOOTBALL_API_KEY -> job tidak dijadwalkan
class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_repeating(self, cb, interval=None, first=None, name=None):
        self.jobs.append(name)


class FakeApp:
    def __init__(self):
        self.handlers = []
        self.job_queue = FakeJobQueue()

    def add_handler(self, h):
        self.handlers.append(h)


os.environ.pop("FOOTBALL_API_KEY", None)
app_no_key = FakeApp()
skor.register(app_no_key, fresh_conn())
_t("tanpa key: job tidak dijadwalkan", app_no_key.job_queue.jobs == [])
_t("register: 4 handler", len(app_no_key.handlers) == 4)

os.environ["FOOTBALL_API_KEY"] = "KUNCI123"
app_key = FakeApp()
skor.register(app_key, fresh_conn())
del os.environ["FOOTBALL_API_KEY"]
_t("dengan key: job tiap jam dijadwalkan",
   app_key.job_queue.jobs == ["skor_auto_settle"])

print("SKOR " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
