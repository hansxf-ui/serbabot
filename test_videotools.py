"""Test tools video: ffmpeg di-MOCK (tidak encode sungguhan)."""
import asyncio
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plugins.tools as toolsmod
from plugins.tools import (
    FFMPEG_TIMEOUT,
    MAX_BYTES,
    TOOL_LABEL,
    VIDEO_ACTIONS,
    vid_compress,
    vid_to_mp4,
    vid_trim,
)

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# --- mock subprocess.run ---
_real_run = subprocess.run
_calls = []


class _FakeProc:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr


def _fake_ok(args, **kw):
    _calls.append((args, kw))
    # bikin file output beneran biar alur handler bisa lanjut
    dst = args[-1]
    with open(dst, "wb") as f:
        f.write(b"FAKEVIDEO")
    return _FakeProc(0)


subprocess.run = _fake_ok

print("== argumen ffmpeg ==")
tmp = tempfile.mkdtemp(prefix="sbvid_")
src = os.path.join(tmp, "in.mp4")
open(src, "wb").write(b"\0" * 100)

_calls.clear()
vid_compress(src, os.path.join(tmp, "c.mp4"))
a = _calls[0][0]
_t("compress: ffmpeg dipanggil", a[0] == "ffmpeg")
_t("compress: scale max 1280", any("1280" in x for x in a))
_t("compress: crf 28", "28" in a[a.index("-crf") + 1])
_t("compress: preset veryfast", "veryfast" in a[a.index("-preset") + 1])
_t("compress: libx264", "libx264" in a)
_t("compress: ada -i dan output", a[a.index("-i") + 1] == src and a[-1].endswith(".mp4"))
_t("compress: timeout 180", _calls[0][1].get("timeout") == FFMPEG_TIMEOUT)

_calls.clear()
vid_to_mp4(src, os.path.join(tmp, "m.mp4"))
a = _calls[0][0]
_t("convert: h264+aac", "libx264" in a and "aac" in a)
_t("convert: faststart", "+faststart" in a)

_calls.clear()
vid_trim(src, os.path.join(tmp, "t.mp4"))
a = _calls[0][0]
_t("trim: -t 30", a[a.index("-t") + 1] == "30")
_t("trim: copy (tanpa re-encode)", "copy" in a[a.index("-c") + 1])

print("== error ffmpeg ==")


def _fake_missing(args, **kw):
    raise FileNotFoundError("ffmpeg")


subprocess.run = _fake_missing
try:
    vid_compress(src, os.path.join(tmp, "x.mp4"))
    _t("ffmpeg hilang -> FFMPEG_OFF", False)
except RuntimeError as e:
    _t("ffmpeg hilang -> FFMPEG_OFF", str(e) == "FFMPEG_OFF")


def _fake_fail(args, **kw):
    return _FakeProc(1, b"boom")


subprocess.run = _fake_fail
try:
    vid_to_mp4(src, os.path.join(tmp, "x.mp4"))
    _t("returncode != 0 -> RuntimeError", False)
except RuntimeError as e:
    _t("returncode != 0 -> RuntimeError", "gagal" in str(e))


def _fake_timeout(args, **kw):
    raise subprocess.TimeoutExpired(cmd=args, timeout=180)


subprocess.run = _fake_timeout
try:
    vid_trim(src, os.path.join(tmp, "x.mp4"))
    _t("timeout -> RuntimeError", False)
except RuntimeError as e:
    _t("timeout -> RuntimeError", "3 menit" in str(e))

subprocess.run = _fake_ok

print("== menu tombol ==")
_t("TOOL_LABEL ada 3 video", all(k in TOOL_LABEL for k in VIDEO_ACTIONS))


class _FakeMsg:
    def __init__(self):
        self.sent = []
        self.last_status = None

    async def reply_text(self, text, reply_markup=None):
        self.sent.append((text, reply_markup))
        self.last_status = _FakeStatus()
        return self.last_status


class _FakeStatus:
    def __init__(self):
        self.edited = []
        self.deleted = False

    async def edit_text(self, t):
        self.edited.append(t)

    async def delete(self):
        self.deleted = True


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMsg()


upd = _FakeUpdate()
asyncio.run(toolsmod.tools_cmd(upd, None))
text, kb = upd.message.sent[0]
btns = [b.text for row in kb.inline_keyboard for b in row]
_t("menu utama ada tombol video", any("video" in b.lower() for b in btns))


class _FakeQ:
    def __init__(self, data):
        self.data = data
        self.edited = []

    async def answer(self):
        pass

    async def edit_message_text(self, text, reply_markup=None):
        self.edited.append((text, reply_markup))


class _FakeQU:
    def __init__(self, data):
        self.callback_query = _FakeQ(data)


class _FakeCtx:
    def __init__(self):
        self.user_data = {}


ctx = _FakeCtx()
qu = _FakeQU("tool:video")
asyncio.run(toolsmod.tool_choice(qu, ctx))
text, kb = qu.callback_query.edited[0]
btns = [b.text for row in kb.inline_keyboard for b in row]
_t("submenu video 3 tombol", sum(1 for b in btns if "video" in b.lower()
                                 or "mp4" in b.lower() or "potong" in b.lower()) >= 3)
_t("submenu ada tombol batal", any("batal" in b.lower() for b in btns))

ctx2 = _FakeCtx()
qu2 = _FakeQU("tool:vcompress")
asyncio.run(toolsmod.tool_choice(qu2, ctx2))
_t("pilih vcompress -> pending_tool", ctx2.user_data.get("pending_tool") == "vcompress")
_t("minta video (bukan foto)", "videonya" in qu2.callback_query.edited[0][0])

ctx3 = _FakeCtx()
qu3 = _FakeQU("tool:pdf")
asyncio.run(toolsmod.tool_choice(qu3, ctx3))
_t("tool foto tetap minta foto", "fotonya" in qu3.callback_query.edited[0][0])

print("== handler video_in (mock penuh) ==")

toolsmod.is_in_session = lambda uid: False
toolsmod.upsert_user = lambda *a: None
toolsmod.is_premium = lambda c, u: False
toolsmod.is_vip = lambda c, u, a=None: False
toolsmod.quota_ok = lambda c, u: True
toolsmod.inc_downloads_today = lambda *a: None


class _FakeTgFile:
    def __init__(self, size):
        self.size = size

    async def download_to_drive(self, dst):
        with open(dst, "wb") as f:
            f.write(b"\0" * self.size)


class _FakeVideo:
    def __init__(self, size):
        self._size = size

    async def get_file(self):
        return _FakeTgFile(self._size)


class _FakeVMsg(_FakeMsg):
    def __init__(self, size):
        super().__init__()
        self.video = _FakeVideo(size)
        self.video_sent = []

    async def reply_video(self, video=None, **kw):
        self.video_sent.append(True)


class _FakeVUpdate:
    def __init__(self, size):
        self.message = _FakeVMsg(size)
        self.effective_user = type("U", (), {"id": 1, "username": "u"})()


class _FakeVCtx(_FakeCtx):
    def __init__(self):
        super().__init__()
        self.bot_data = {"db": None}


# file > 45MB ditolak, ffmpeg TIDAK dipanggil
_calls.clear()
big = _FakeVUpdate(MAX_BYTES + 1024)
bctx = _FakeVCtx()
bctx.user_data["pending_tool"] = "vcompress"
asyncio.run(toolsmod.video_in(big, bctx))
status = big.message.last_status
_t("file >45MB: ffmpeg tidak dipanggil", len(_calls) == 0)
_t("file >45MB: pesan kegedean", any("kegedean" in e for e in status.edited))
_t("file >45MB: tidak kirim video", len(big.message.video_sent) == 0)

# happy path: video kecil -> ffmpeg jalan -> video kekirim
_calls.clear()
small = _FakeVUpdate(1024)
sctx = _FakeVCtx()
sctx.user_data["pending_tool"] = "vtrim"
asyncio.run(toolsmod.video_in(small, sctx))
_t("happy path: ffmpeg dipanggil", len(_calls) == 1)
_t("happy path: video kekirim", len(small.message.video_sent) == 1)
_t("happy path: pending_tool dibersihkan", "pending_tool" not in sctx.user_data)
status = small.message.last_status
_t("happy path: status dihapus", status.deleted)

# video masuk tapi pending_tool-nya tool foto -> diabaikan
_calls.clear()
wrong = _FakeVUpdate(1024)
wctx = _FakeVCtx()
wctx.user_data["pending_tool"] = "pdf"
asyncio.run(toolsmod.video_in(wrong, wctx))
_t("pending foto + kirim video -> diabaikan", len(_calls) == 0
   and len(wrong.message.sent) == 0)

# FFMPEG_OFF -> pesan ramah
subprocess.run = _fake_missing
off = _FakeVUpdate(1024)
octx = _FakeVCtx()
octx.user_data["pending_tool"] = "vconvert"
asyncio.run(toolsmod.video_in(off, octx))
status = off.message.last_status
_t("FFMPEG_OFF: pesan ramah", any("belum aktif" in e for e in status.edited))
subprocess.run = _fake_ok

print("VIDEO TOOLS OK" if not fails else "VIDEO TOOLS FAIL: %s" % fails)
sys.exit(1 if fails else 0)
