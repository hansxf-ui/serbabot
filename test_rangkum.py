"""Test plugin rangkum: fetch_article + /rangkum flow.

Semua di-mock (urllib + Telegram + ask_gemini). Tidak ada network sungguhan.
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.rangkum as rangkum

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
        return self  # jadi "status message" yang bisa di-edit

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


# ---------- fake HTTP ----------

class FakeResp:
    def __init__(self, text):
        self._t = text

    def read(self):
        return self._t.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


ARTICLE_HTML = """<html><head><title>Berita Heboh Hari Ini</title>
<style>.x{color:red}</style></head><body>
<script>alert('evil')</script>
<p>Paragraf pertama yang cukup panjang buat lolos batas minimal
karakter yang dipersyaratkan oleh fungsi fetch_article di plugin ini.</p>
<p>Paragraf <b>kedua</b> dengan <a href="/x">link</a> dan &amp; entity.</p>
</body></html>"""

http_calls = []


def fake_urlopen(req, timeout=None):
    http_calls.append(req.full_url)
    if req.full_url == "https://gagal.test/x":
        raise urllib.error.URLError("connection refused")
    if req.full_url == "https://pendek.test/x":
        return FakeResp("<html><title>Hi</title><body><p>pendek</p></body></html>")
    return FakeResp(ARTICLE_HTML)


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen

print("== rangkum ==")

# 1. fetch_article: strip HTML, judul kebawa, batas 8000 char
t1 = rangkum.fetch_article("https://contoh.test/berita")
_t("judul kebawa", "Berita Heboh Hari Ini" in t1)
_t("script/style dibuang", "alert" not in t1 and "color:red" not in t1)
_t("tag p dibuang", "<b>" not in t1 and "<a " not in t1)
_t("entity di-decode", "& entity" in t1)
_t("dibatas 8000 char", len(t1) <= rangkum.MAX_CHARS)

big = "<html><head><title>T</title></head><body>" + \
    ("<p>" + "x" * 100 + "</p>") * 200 + "</body></html>"

def fake_big(req, timeout=None):
    return FakeResp(big)

urllib.request.urlopen = fake_big
t_big = rangkum.fetch_article("https://besar.test/x")
urllib.request.urlopen = fake_urlopen
_t("artikel raksasa dipotong tepat 8000", len(t_big) == rangkum.MAX_CHARS)

# 2. URL invalid -> ValueError
try:
    rangkum.fetch_article("bukan-url")
    _t("URL invalid: raise ValueError", False)
except ValueError:
    _t("URL invalid: raise ValueError", True)

# 3. fetch gagal -> RuntimeError pesan ramah
try:
    rangkum.fetch_article("https://gagal.test/x")
    _t("fetch gagal: raise RuntimeError", False)
except RuntimeError as e:
    _t("fetch gagal: raise RuntimeError", "Coba lagi" in str(e))

# 4. konten < 200 char -> RuntimeError
try:
    rangkum.fetch_article("https://pendek.test/x")
    _t("konten pendek: raise RuntimeError", False)
except RuntimeError as e:
    _t("konten pendek: raise RuntimeError", "terlalu pendek" in str(e))

# ---------- /rangkum flow ----------

gemini_calls = []


def fake_ask_gemini(system_prompt, question, api_key, max_tokens=1000):
    gemini_calls.append({"sp": system_prompt, "q": question,
                         "key": api_key, "mt": max_tokens})
    return "Ringkasan: 1. bla 2. bla 3. bla 4. bla 5. bla"


rangkum.ask_gemini, saved_ask = fake_ask_gemini, rangkum.ask_gemini
os.environ["GEMINI_API_KEY"] = "TESTKEY123"
conn = db.init_db(":memory:")

# 5. sukses: fetch -> AI -> ringkasan terkirim, jatah kepotong 1
upd = FakeUpdate(111)
asyncio.run(rangkum.rangkum_cmd(upd, FakeContext(conn, ["https://contoh.test/berita"])))
_t("status message dikirim", any("baca artikel" in r for r in upd.message.replies))
_t("ringkasan terkirim", upd.message.edits[-1].startswith("Ringkasan:"))
_t("system prompt 5 poin", "5 poin" in gemini_calls[0]["sp"])
_t("teks artikel dikirim ke AI", "Paragraf pertama" in gemini_calls[0]["q"])
_t("max_tokens 800", gemini_calls[0]["mt"] == 800)
_t("api key kekirim", gemini_calls[0]["key"] == "TESTKEY123")
_t("jatah rangkum kepotong 1", db.get_quota_today(conn, "rangkum", 111) == 1)
_t("jatah fitur lain tidak kepotong", db.get_quota_today(conn, "voice", 111) == 0)

# 6. env kosong -> pesan jujur, HTTP tidak dipanggil, jatah aman
del os.environ["GEMINI_API_KEY"]
http_calls.clear()
upd2 = FakeUpdate(222)
asyncio.run(rangkum.rangkum_cmd(upd2, FakeContext(conn, ["https://contoh.test/berita"])))
_t("tanpa key: pesan jujur", any("butuh GEMINI_API_KEY" in r
                                  and "minta admin" in r for r in upd2.message.replies))
_t("tanpa key: HTTP tidak dipanggil", http_calls == [])
_t("tanpa key: jatah tidak kepotong", db.get_quota_today(conn, "rangkum", 222) == 0)
os.environ["GEMINI_API_KEY"] = "TESTKEY123"

# 7. tanpa URL -> cara pakai, jatah aman
upd3 = FakeUpdate(333)
asyncio.run(rangkum.rangkum_cmd(upd3, FakeContext(conn, [])))
_t("tanpa URL: kasih cara pakai", any("/rangkum <url>" in r for r in upd3.message.replies))
_t("tanpa URL: jatah tidak kepotong", db.get_quota_today(conn, "rangkum", 333) == 0)

# 8. jatah habis -> ditolak sebelum fetch
for _ in range(5):
    db.inc_quota_today(conn, "rangkum", 444)
http_calls.clear()
upd4 = FakeUpdate(444)
asyncio.run(rangkum.rangkum_cmd(upd4, FakeContext(conn, ["https://contoh.test/x"])))
_t("jatah habis: ditolak", any("habis" in r for r in upd4.message.replies))
_t("jatah habis: fetch tidak dipanggil", http_calls == [])

# 9. fetch gagal -> pesan ramah, jatah tidak kepotong
upd5 = FakeUpdate(555)
asyncio.run(rangkum.rangkum_cmd(upd5, FakeContext(conn, ["https://gagal.test/x"])))
_t("fetch gagal: pesan ramah", any("Gagal fetch" in e for e in upd5.message.edits))
_t("fetch gagal: jatah tidak kepotong", db.get_quota_today(conn, "rangkum", 555) == 0)

# 10. AI error -> pesan ramah, jatah tidak kepotong
def fake_ask_boom(sp, q, key, max_tokens=1000):
    raise RuntimeError("AI mati")

rangkum.ask_gemini = fake_ask_boom
upd6 = FakeUpdate(666)
asyncio.run(rangkum.rangkum_cmd(upd6, FakeContext(conn, ["https://contoh.test/berita"])))
_t("AI error: pesan ramah", any("lagi error" in e for e in upd6.message.edits))
_t("AI error: jatah tidak kepotong", db.get_quota_today(conn, "rangkum", 666) == 0)
rangkum.ask_gemini = fake_ask_gemini

# 11. VIP: unlimited, jatah tidak dicatat
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(777,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
for _ in range(5):
    db.inc_quota_today(conn, "rangkum", 777)
http_calls.clear()
upd7 = FakeUpdate(777)
asyncio.run(rangkum.rangkum_cmd(upd7, FakeContext(conn, ["https://contoh.test/berita"])))
_t("VIP: tetap dilayani", http_calls != [] and upd7.message.edits[-1].startswith("Ringkasan:"))
_t("VIP: jatah tidak dicatat", db.get_quota_today(conn, "rangkum", 777) == 5)

rangkum.ask_gemini = saved_ask
del os.environ["GEMINI_API_KEY"]
urllib.request.urlopen = real_urlopen

print("RANGKUM " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
