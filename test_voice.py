"""Test plugin voice: /vn + transkrip VN.

Semua di-mock (urllib + Telegram + edge_tts via sys.modules stub).
Tidak ada network sungguhan.
"""
import asyncio
import base64
import json
import os
import sys
import types
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import plugins.aichat as aichat
import plugins.voice as voice

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
        self.voices = []
        self.voice = None

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return self  # jadi "status message" yang bisa di-edit

    async def edit_text(self, text, **kw):
        self.edits.append(text)

    async def reply_voice(self, voice, **kw):
        self.voices.append(voice.read())


class FakeVoice:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeTGFile:
    def __init__(self):
        self.downloaded_to = None

    async def download_to_drive(self, path):
        self.downloaded_to = path
        with open(path, "wb") as f:
            f.write(b"FAKE-OGG-BYTES")


class FakeBot:
    def __init__(self):
        self.tg_file = FakeTGFile()

    async def get_file(self, file_id):
        self.file_id = file_id
        return self.tg_file


class FakeUpdate:
    def __init__(self, uid):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, conn, args):
        self.args = args
        self.bot_data = {"db": conn}
        self.bot = FakeBot()


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


GEMINI_JSON = json.dumps({"candidates": [{"content": {"parts": [
    {"text": "halo, ini transkrip VN"}]}}]})
captured = {}
http_calls = []


def fake_urlopen(req, timeout=None):
    http_calls.append(req.full_url)
    captured["url"] = req.full_url
    captured["key"] = req.get_header("X-goog-api-key")
    captured["body"] = json.loads(req.data.decode())
    return FakeResp(GEMINI_JSON)


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
# skip discovery Gemini di semua test voice
aichat._GEMINI_MODEL_CACHE = ["gemini-2.5-flash"]

print("== voice ==")

# 1. transcribe_voice: request dibentuk benar
out = voice.transcribe_voice(b"FAKE-OGG-BYTES", "TESTKEY123")
parts = captured["body"]["contents"][0]["parts"]
audio_part = parts[1]["inline_data"]
_t("model flash dipakai",
   captured["url"] == aichat.GEMINI_API_BASE + "/gemini-2.5-flash:generateContent")
_t("api key kekirim", captured["key"] == "TESTKEY123")
_t("inline_data audio/ogg", audio_part["mime_type"] == "audio/ogg")
_t("audio di-base64 utuh",
   base64.b64decode(audio_part["data"]) == b"FAKE-OGG-BYTES")
_t("ada prompt transkrip", "Transkrip" in parts[0]["text"])
_t("teks transkrip dibalikin", out == "halo, ini transkrip VN")

# 2. model 404 di-skip, lanjut ke model berikutnya
tried2 = []

def fake_404_skip(req, timeout=None):
    tried2.append(req.full_url)
    if "gemini-2.5-flash:" in req.full_url:
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    return FakeResp(GEMINI_JSON)

urllib.request.urlopen = fake_404_skip
out2 = voice.transcribe_voice(b"x", "K")
urllib.request.urlopen = fake_urlopen
_t("404: lanjut ke model berikutnya",
   len(tried2) == 2 and "gemini-2.5-flash-lite:generateContent" in tried2[1])
_t("404: transkrip dari model pengganti sampai", out2 == "halo, ini transkrip VN")

# 3. semua model gagal -> raise
def fake_all_fail(req, timeout=None):
    raise urllib.error.HTTPError(req.full_url, 503, "Down", {}, None)

urllib.request.urlopen = fake_all_fail
try:
    voice.transcribe_voice(b"x", "K")
    _t("semua model gagal: raise", False)
except Exception:
    _t("semua model gagal: raise", True)
urllib.request.urlopen = fake_urlopen

# ---------- /vn flow ----------

sys.modules.pop("edge_tts", None)  # pastikan modul beneran nggak ada
conn = db.init_db(":memory:")

# 4. edge_tts belum diinstall -> pesan jujur, jatah aman
upd = FakeUpdate(111)
asyncio.run(voice.vn_cmd(upd, FakeContext(conn, ["halo"])))
_t("tanpa edge_tts: pesan jujur",
   any("belum aktif di server" in r for r in upd.message.replies))
_t("tanpa edge_tts: jatah tidak kepotong", db.get_quota_today(conn, "voice", 111) == 0)

# ---------- stub edge_tts ----------

tts_calls = []


class FakeCommunicate:
    def __init__(self, text, voice):
        tts_calls.append({"text": text, "voice": voice})

    async def save(self, path):
        with open(path, "wb") as f:
            f.write(b"FAKE-MP3-BYTES")


edge_stub = types.ModuleType("edge_tts")
edge_stub.Communicate = FakeCommunicate
sys.modules["edge_tts"] = edge_stub

# 5. tts_generate: dipanggil langsung (async), nulis file
path5 = "/tmp/test_voice_tts.mp3"
asyncio.run(voice.tts_generate("halo dunia", path5))
_t("tts_generate: file mp3 ditulis",
   os.path.exists(path5) and open(path5, "rb").read() == b"FAKE-MP3-BYTES")
_t("tts_generate: suara id-ID-ArdiNeural", tts_calls[-1]["voice"] == "id-ID-ArdiNeural")
_t("tts_generate: teks utuh", tts_calls[-1]["text"] == "halo dunia")
os.unlink(path5)

# 6. /vn sukses: VN terkirim, file temp dibersihin, jatah kepotong 1
os.environ["GEMINI_API_KEY"] = "TESTKEY123"
upd2 = FakeUpdate(222)
asyncio.run(voice.vn_cmd(upd2, FakeContext(conn, ["apa", "kabar?"])))
_t("vn: voice terkirim", upd2.message.voices == [b"FAKE-MP3-BYTES"])
_t("vn: jatah voice kepotong 1", db.get_quota_today(conn, "voice", 222) == 1)

# 7. teks > 500 char -> ditolak
upd3 = FakeUpdate(333)
asyncio.run(voice.vn_cmd(upd3, FakeContext(conn, ["x" * 501])))
_t("teks >500: ditolak", any("kepanjangan" in r for r in upd3.message.replies))
_t("teks >500: jatah aman", db.get_quota_today(conn, "voice", 333) == 0)

# 8. tanpa teks -> cara pakai
upd4 = FakeUpdate(444)
asyncio.run(voice.vn_cmd(upd4, FakeContext(conn, [])))
_t("tanpa teks: kasih cara pakai",
   any("/vn <teks>" in r for r in upd4.message.replies))

# 9. jatah voice habis -> ditolak, TTS tidak dipanggil
for _ in range(3):
    db.inc_quota_today(conn, "voice", 555)
tts_calls.clear()
upd5 = FakeUpdate(555)
asyncio.run(voice.vn_cmd(upd5, FakeContext(conn, ["halo"])))
_t("jatah habis: ditolak", any("habis" in r for r in upd5.message.replies))
_t("jatah habis: TTS tidak dipanggil", tts_calls == [])

# ---------- transkrip VN masuk ----------

# 10. tanpa API key -> pesan jujur, download tidak dipanggil
del os.environ["GEMINI_API_KEY"]
upd6 = FakeUpdate(666)
upd6.message.voice = FakeVoice("fid-1")
ctx6 = FakeContext(conn, [])
asyncio.run(voice.voice_in(upd6, ctx6))
_t("tanpa key: pesan jujur",
   any("butuh API key" in r and "minta admin" in r for r in upd6.message.replies))
_t("tanpa key: file tidak di-download", ctx6.bot.tg_file.downloaded_to is None)
_t("tanpa key: jatah aman", db.get_quota_today(conn, "voice", 666) == 0)
os.environ["GEMINI_API_KEY"] = "TESTKEY123"

# 11. sukses: VN di-download, transkrip terkirim, jatah kepotong 1
upd7 = FakeUpdate(777)
upd7.message.voice = FakeVoice("fid-2")
ctx7 = FakeContext(conn, [])
asyncio.run(voice.voice_in(upd7, ctx7))
_t("vn masuk: status dikirim", any("dengerin" in r for r in upd7.message.replies))
_t("vn masuk: file di-download ke temp ogg",
   (ctx7.bot.tg_file.downloaded_to or "").endswith(".ogg"))
_t("vn masuk: transkrip terkirim",
   any("halo, ini transkrip VN" in e for e in upd7.message.edits))
_t("vn masuk: jatah kepotong 1", db.get_quota_today(conn, "voice", 777) == 1)

# 12. jatah GABUNG vn+transkrip: 2 vn + 1 transkrip = 3 -> ke-4 ditolak
upd8 = FakeUpdate(888)
upd8.message.voice = FakeVoice("fid-3")
asyncio.run(voice.vn_cmd(upd8, FakeContext(conn, ["satu"])))
asyncio.run(voice.vn_cmd(upd8, FakeContext(conn, ["dua"])))
asyncio.run(voice.voice_in(upd8, FakeContext(conn, [])))
_t("jatah gabung: 2 vn + 1 transkrip = 3", db.get_quota_today(conn, "voice", 888) == 3)
upd8b = FakeUpdate(888)
upd8b.message.voice = FakeVoice("fid-4")
asyncio.run(voice.voice_in(upd8b, FakeContext(conn, [])))
_t("jatah gabung: transkrip ke-4 ditolak",
   any("habis" in r for r in upd8b.message.replies))

# 13. AI error -> pesan ramah, jatah tidak kepotong
urllib.request.urlopen = fake_all_fail
upd9 = FakeUpdate(999)
upd9.message.voice = FakeVoice("fid-5")
asyncio.run(voice.voice_in(upd9, FakeContext(conn, [])))
urllib.request.urlopen = fake_urlopen
_t("transkrip gagal: pesan ramah",
   any("gagal" in e for e in upd9.message.edits))
_t("transkrip gagal: jatah tidak kepotong", db.get_quota_today(conn, "voice", 999) == 0)

# 14. VIP: unlimited, jatah tidak dicatat
conn.execute("INSERT INTO users(user_id, username, is_premium, created_at)"
             " VALUES(1000,'p',1,'x') ON CONFLICT(user_id) DO UPDATE SET is_premium=1")
conn.commit()
for _ in range(3):
    db.inc_quota_today(conn, "voice", 1000)
upd10 = FakeUpdate(1000)
upd10.message.voice = FakeVoice("fid-6")
asyncio.run(voice.voice_in(upd10, FakeContext(conn, [])))
_t("VIP: tetap ditranskrip",
   any("halo, ini transkrip VN" in e for e in upd10.message.edits))
_t("VIP: jatah tidak dicatat", db.get_quota_today(conn, "voice", 1000) == 3)

sys.modules.pop("edge_tts", None)
del os.environ["GEMINI_API_KEY"]
urllib.request.urlopen = real_urlopen
aichat._GEMINI_MODEL_CACHE = None

print("VOICE " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
