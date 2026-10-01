"""Test plugin tools: fungsi murni (tanpa Telegram)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image as PILImage

from plugins.tools import img_compress, img_convert, img_to_pdf

fails = []


def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


print("== tools pure ==")
tmp = tempfile.mkdtemp(prefix="sbtools_")
src = os.path.join(tmp, "in.png")
PILImage.new("RGB", (200, 100), "red").save(src)

d = os.path.join(tmp, "o.pdf")
img_to_pdf(src, d)
_t("pdf kebuat", os.path.exists(d) and os.path.getsize(d) > 0)

for fmt, ext in [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")]:
    d = os.path.join(tmp, "o." + ext)
    img_convert(src, d, fmt)
    _t("convert %s" % fmt, os.path.exists(d) and os.path.getsize(d) > 0)

big = os.path.join(tmp, "big.jpg")
PILImage.new("RGB", (3000, 2000), "blue").save(big, quality=95)
d = os.path.join(tmp, "c.jpg")
img_compress(big, d)
_t("kompres lebih kecil", os.path.getsize(d) < os.path.getsize(big))
with PILImage.open(d) as im:
    _t("kompres max 1600px", max(im.size) <= 1600)

try:
    from plugins.tools import img_rmbg

    d = os.path.join(tmp, "nobg.png")
    try:
        img_rmbg(src, d)
        _t("rmbg jalan", os.path.exists(d) and os.path.getsize(d) > 0)
    except Exception as e:  # model gagal diunduh / tanpa internet
        print("  SKIP rmbg jalan (%s)" % e)
except ImportError:
    print("  SKIP rmbg (rembg tidak diinstall)")


# --- rembg rusak/ngamuk: plugin tetap ke-import, bot tidak mati ---
print("== rembg isolation ==")
import importlib
import plugins.tools as toolsmod


class _BrokenRembg:
    def __getattr__(self, name):
        raise RuntimeError("simulasi rembg ngamuk")


sys.modules["rembg"] = _BrokenRembg()
importlib.reload(toolsmod)
_t("plugin ke-import walau rembg rusak", True)
_t("_get_rembg balikin None", toolsmod._get_rembg() is None)
try:
    toolsmod.img_rmbg("/tmp/x", "/tmp/y")
    _t("img_rmbg raise ramah", False)
except RuntimeError as e:
    _t("img_rmbg raise ramah", "belum bisa dipakai" in str(e))
del sys.modules["rembg"]
importlib.reload(toolsmod)  # balikin normal
print("ISOLATION OK")

# --- Replicate API (mock urlopen) ---
print("== replicate rmbg ==")
import io as _io
import json as _json
import urllib.request as _urlreq
import urllib.error as _urlerr

tools = toolsmod

os.environ.pop("REPLICATE_API_TOKEN", None)
try:
    tools._replicate_rmbg("/tmp/x.jpg", "/tmp/y.png")
    _t("tanpa token -> NO_REPLICATE_TOKEN", False)
except RuntimeError as e:
    _t("tanpa token -> NO_REPLICATE_TOKEN", str(e) == "NO_REPLICATE_TOKEN")

os.environ["REPLICATE_API_TOKEN"] = "r8_test_token"

_rep_src = "/tmp/rep_test_in.jpg"
PILImage.new("RGB", (800, 600), "green").save(_rep_src, quality=90)


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self, *a):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_real_urlopen = _urlreq.urlopen
_sent_bodies = []


def _fake_ok(req, timeout=None):
    url = req.full_url if isinstance(req, _urlreq.Request) else req
    if url == tools.REPLICATE_API:
        _sent_bodies.append(req.data)
        return _FakeResp(_json.dumps(
            {"id": "pred123", "status": "succeeded",
             "output": "https://example.com/hasil.png"}).encode())
    if url == "https://example.com/hasil.png":
        return _FakeResp(b"\x89PNG\r\n\x1afake-png-bytes")
    raise AssertionError("URL tak terduga: " + url)


_urlreq.urlopen = _fake_ok
try:
    dst = "/tmp/rep_test_out.png"
    if os.path.exists(dst):
        os.remove(dst)
    tools._replicate_rmbg("/tmp/rep_test_in.jpg", dst)
    _t("sukses langsung (Prefer: wait)", os.path.exists(dst))
    with open(dst, "rb") as f:
        _t("isi hasil kepindah", f.read() == b"\x89PNG\r\n\x1afake-png-bytes")
    body = _json.loads(_sent_bodies[0])
    _t("kirim data-uri image", body["input"]["image"].startswith("data:image/jpeg;base64,"))
finally:
    _urlreq.urlopen = _real_urlopen


# alur: processing -> poll 1x -> succeeded (output bentuk list)
_poll_n = {"n": 0}


def _fake_poll(req, timeout=None):
    url = req.full_url if isinstance(req, _urlreq.Request) else req
    if url == tools.REPLICATE_API:
        return _FakeResp(_json.dumps({"id": "p1", "status": "processing"}).encode())
    if url == "https://api.replicate.com/v1/predictions/p1":
        _poll_n["n"] += 1
        st = "processing" if _poll_n["n"] < 2 else "succeeded"
        return _FakeResp(_json.dumps(
            {"id": "p1", "status": st,
             "output": ["https://example.com/h2.png"]}).encode())
    if url == "https://example.com/h2.png":
        return _FakeResp(b"png2")
    raise AssertionError("URL tak terduga: " + url)


_urlreq.urlopen = _fake_poll
try:
    dst2 = "/tmp/rep_test_out2.png"
    if os.path.exists(dst2):
        os.remove(dst2)
    tools._replicate_rmbg("/tmp/rep_test_in.jpg", dst2, timeout=30)
    _t("polling sampai succeeded", os.path.exists(dst2))
    _t("output list di-unwrap", open(dst2, "rb").read() == b"png2")
    _t("poll dipanggil", _poll_n["n"] >= 2)
finally:
    _urlreq.urlopen = _real_urlopen


# 401 -> RuntimeError informatif
def _fake_401(req, timeout=None):
    raise _urlerr.HTTPError(req.full_url, 401, "Unauthorized", {},
                            _io.BytesIO(b"invalid token"))


_urlreq.urlopen = _fake_401
try:
    try:
        tools._replicate_rmbg("/tmp/rep_test_in.jpg", "/tmp/zz.png")
        _t("401 -> RuntimeError", False)
    except RuntimeError as e:
        _t("401 -> RuntimeError", "401" in str(e))
finally:
    _urlreq.urlopen = _real_urlopen

os.environ.pop("REPLICATE_API_TOKEN", None)
print("REPLICATE OK")

print("TOOLS OK" if not fails else "TOOLS FAIL: %s" % fails)
sys.exit(1 if fails else 0)
