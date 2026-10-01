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

print("TOOLS OK" if not fails else "TOOLS FAIL: %s" % fails)
sys.exit(1 if fails else 0)
