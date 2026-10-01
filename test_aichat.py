"""Test plugin aichat: quota dicek, request dibentuk benar, timeout ditangani.

Semua di-mock (urllib + Telegram). Tidak ada network sungguhan.
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.aichat as aichat

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


captured = {}
http_calls = []


def fake_urlopen(req, timeout=None):
    http_calls.append(req.full_url)
    captured["url"] = req.full_url
    captured["method"] = req.get_method()
    captured["body"] = json.loads(req.data.decode())
    captured["timeout"] = timeout
    return FakeResp("Jawaban AI: halo!")


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen

print("== aichat ==")

# 1. sukses: request dibentuk benar + jatah kepotong 1
conn = db.init_db(":memory:")
upd = FakeUpdate(111)
asyncio.run(aichat.ai_cmd(upd, FakeContext(conn, ["apa", "kabar?"])))
_t("request ke text.pollinations.ai", captured.get("url") == "https://text.pollinations.ai/")
_t("method POST", captured.get("method") == "POST")
_t("model openai (bukan default halu)", captured.get("body", {}).get("model") == "openai")
msgs = captured.get("body", {}).get("messages", [])
_t("ada system prompt", len(msgs) == 2 and msgs[0]["role"] == "system"
   and "Indonesia" in msgs[0]["content"])
_t("pertanyaan user kekirim utuh", len(msgs) == 2 and msgs[1]["role"] == "user"
   and msgs[1]["content"] == "apa kabar?")
_t("jawaban diteruskan ke user", upd.message.edits == ["Jawaban AI: halo!"])
_t("jatah kepotong 1", db.get_downloads_today(conn, 111) == 1)

# 2. tanpa pertanyaan -> pesan cara pakai, jatah aman, tanpa HTTP
http_calls.clear()
upd2 = FakeUpdate(222)
asyncio.run(aichat.ai_cmd(upd2, FakeContext(conn, [])))
_t("tanpa argumen: kasih cara pakai",
   any("/ai <pertanyaan>" in r for r in upd2.message.replies))
_t("tanpa argumen: tidak panggil HTTP", http_calls == [])
_t("tanpa argumen: jatah tidak kepotong", db.get_downloads_today(conn, 222) == 0)

# 3. jatah habis -> ditolak, HTTP tidak dipanggil
for _ in range(5):
    db.inc_downloads_today(conn, 333)
http_calls.clear()
upd3 = FakeUpdate(333)
asyncio.run(aichat.ai_cmd(upd3, FakeContext(conn, ["halo"])))
_t("jatah habis: ditolak", any("habis" in r for r in upd3.message.replies))
_t("jatah habis: HTTP tidak dipanggil", http_calls == [])

# 4. timeout -> pesan ramah, jatah tidak kepotong
def _boom(q):
    raise TimeoutError("lambat")

aichat._ask_ai, saved_ask = _boom, aichat._ask_ai
upd4 = FakeUpdate(444)
asyncio.run(aichat.ai_cmd(upd4, FakeContext(conn, ["halo"])))
aichat._ask_ai = saved_ask
_t("timeout: pesan ramah", any("kelamaan" in e for e in upd4.message.edits))
_t("timeout: jatah tidak kepotong", db.get_downloads_today(conn, 444) == 0)

# 5. jawaban kepanjangan -> dipotong
def fake_long(req, timeout=None):
    return FakeResp("x" * 5000)

urllib.request.urlopen = fake_long
upd5 = FakeUpdate(555)
asyncio.run(aichat.ai_cmd(upd5, FakeContext(conn, ["halo"])))
urllib.request.urlopen = fake_urlopen
_t("jawaban panjang dipotong",
   len(upd5.message.edits) == 1
   and len(upd5.message.edits[0]) == aichat.MAX_REPLY + 3
   and upd5.message.edits[0].endswith("..."))
_t("sukses tetap makan jatah", db.get_downloads_today(conn, 555) == 1)

# 6. premium -> unlimited, jatah tidak dicatat
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(666,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
for _ in range(5):
    db.inc_downloads_today(conn, 666)
http_calls.clear()
upd6 = FakeUpdate(666)
asyncio.run(aichat.ai_cmd(upd6, FakeContext(conn, ["halo"])))
_t("premium: tetap dilayani walau jatah penuh", http_calls != [])
_t("premium: jawaban sampai", upd6.message.edits == ["Jawaban AI: halo!"])

# 7. Gemini: key di-set -> discovery dulu, lalu generate via model terbaru
GEMINI_JSON = json.dumps({"candidates": [{"content": {"parts": [
    {"text": "Jawaban Gemini: halo!"}, {"text": " Lanjutan."}]}}]})
DISCOVERY_JSON = json.dumps({"models": [
    {"name": "models/gemini-2.0-flash",
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-flash",
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-2.5-pro",
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/text-embedding-004",
     "supportedGenerationMethods": ["embedContent"]},
]})

gen_calls = []

def fake_gemini_discovery(req, timeout=None):
    if req.data is None:  # GET = discovery
        captured["d_url"] = req.full_url
        captured["d_key"] = req.get_header("X-goog-api-key")
        captured["d_method"] = req.get_method()
        return FakeResp(DISCOVERY_JSON)
    gen_calls.append(req.full_url)
    captured["g_url"] = req.full_url
    captured["g_key"] = req.get_header("X-goog-api-key")
    captured["g_body"] = json.loads(req.data.decode())
    return FakeResp(GEMINI_JSON)

os.environ["GEMINI_API_KEY"] = "TESTKEY123"
aichat._GEMINI_MODEL_CACHE = None
urllib.request.urlopen = fake_gemini_discovery
upd7 = FakeUpdate(777)
asyncio.run(aichat.ai_cmd(upd7, FakeContext(conn, ["halo"])))
_t("gemini: discovery ke /v1beta/models",
   captured.get("d_url") == aichat.GEMINI_API_BASE
   and captured.get("d_method") == "GET")
_t("gemini: api key kekirim di discovery", captured.get("d_key") == "TESTKEY123")
_t("gemini: pilih flash versi tertinggi (bukan pro/embedding)",
   captured.get("g_url") == aichat.GEMINI_API_BASE + "/gemini-2.5-flash:generateContent")
_t("gemini: api key kekirim di generate", captured.get("g_key") == "TESTKEY123")
_t("gemini: pertanyaan kekirim",
   captured.get("g_body", {}).get("contents", [{}])[0]
   .get("parts", [{}])[0].get("text") == "halo")
_t("gemini: jawaban digabung & diteruskan",
   upd7.message.edits == ["Jawaban Gemini: halo! Lanjutan."])
_t("gemini: jatah kepotong", db.get_downloads_today(conn, 777) == 1)
_t("gemini: cuma 1x generate (model pertama langsung OK)", len(gen_calls) == 1)

# 8. Model 404 (pensiun) di-skip, lanjut ke model berikutnya
tried8 = []

def fake_gemini_404_skip(req, timeout=None):
    if req.data is None:
        return FakeResp(DISCOVERY_JSON)
    tried8.append(req.full_url)
    if "gemini-2.5-flash" in req.full_url:
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    return FakeResp(GEMINI_JSON)

aichat._GEMINI_MODEL_CACHE = None
urllib.request.urlopen = fake_gemini_404_skip
upd8 = FakeUpdate(888)
asyncio.run(aichat.ai_cmd(upd8, FakeContext(conn, ["halo"])))
_t("404: model pensiun dicoba lalu di-skip",
   len(tried8) == 2
   and "gemini-2.5-flash:generateContent" in tried8[0]
   and "gemini-2.0-flash:generateContent" in tried8[1])
_t("404: jawaban dari model pengganti sampai",
   upd8.message.edits == ["Jawaban Gemini: halo! Lanjutan."])
_t("404: jatah kepotong", db.get_downloads_today(conn, 888) == 1)

# 9. Discovery di-cache: 2x tanya cuma 1x GET models
aichat._GEMINI_MODEL_CACHE = None
d_calls = {"n": 0}

def fake_discovery_count(req, timeout=None):
    if req.data is None:
        d_calls["n"] += 1
        return FakeResp(DISCOVERY_JSON)
    return FakeResp(GEMINI_JSON)

urllib.request.urlopen = fake_discovery_count
aichat._ask_gemini("halo", "K")
aichat._ask_gemini("halo", "K")
_t("discovery: di-cache per proses", d_calls["n"] == 1)

# 10. Discovery gagal -> pakai cascade hardcoded
aichat._GEMINI_MODEL_CACHE = None

def fake_discovery_fail(req, timeout=None):
    if req.data is None:
        raise RuntimeError("network blip")
    captured["c_url"] = req.full_url
    return FakeResp(GEMINI_JSON)

urllib.request.urlopen = fake_discovery_fail
aichat._ask_gemini("halo", "K")
_t("cascade: fallback ke gemini-2.5-flash",
   captured.get("c_url") == aichat.GEMINI_API_BASE + "/gemini-2.5-flash:generateContent")

# 12. Model 503 (overload) di-skip, lanjut ke model berikutnya
aichat._GEMINI_MODEL_CACHE = None
tried12 = []

def fake_gemini_503_skip(req, timeout=None):
    if req.data is None:
        return FakeResp(DISCOVERY_JSON)
    tried12.append(req.full_url)
    if "gemini-2.5-flash:" in req.full_url:
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)
    return FakeResp(GEMINI_JSON)

urllib.request.urlopen = fake_gemini_503_skip
ans12 = aichat._ask_gemini("halo", "K")
_t("503: model overload dicoba lalu di-skip",
   len(tried12) == 2
   and "gemini-2.5-flash:generateContent" in tried12[0]
   and "gemini-2.0-flash:generateContent" in tried12[1])
_t("503: jawaban dari model sehat sampai",
   ans12 == "Jawaban Gemini: halo! Lanjutan.")

# 11. Semua model Gemini 404 -> fallback ke Pollinations
aichat._GEMINI_MODEL_CACHE = None

def fake_all_404_then_poll(req, timeout=None):
    if req.full_url == "https://text.pollinations.ai/":
        return FakeResp("Jawaban fallback: halo!")
    if req.data is None:
        return FakeResp(DISCOVERY_JSON)
    raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

urllib.request.urlopen = fake_all_404_then_poll
upd11 = FakeUpdate(999)
asyncio.run(aichat.ai_cmd(upd11, FakeContext(conn, ["halo"])))
_t("fallback: jawaban pollinations sampai",
   upd11.message.edits == ["Jawaban fallback: halo!"])

del os.environ["GEMINI_API_KEY"]
urllib.request.urlopen = real_urlopen

print("AICHAT " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
