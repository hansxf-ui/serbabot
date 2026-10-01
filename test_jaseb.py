"""Tes plugin jaseb (jasa sebar otomatis)."""
import asyncio
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "plugins/jaseb")

import db
import importlib.util
spec = importlib.util.spec_from_file_location(
    "jaseb", "plugins/jaseb/__init__.py")
jaseb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jaseb)

fails = []

def _t(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


class FakeChat:
    def __init__(self, type="private", id=1, title="", username=""):
        self.type = type
        self.id = id
        self.title = title
        self.username = username


class FakeUser:
    def __init__(self, id, username="u"):
        self.id = id
        self.username = username


class FakePhoto:
    def __init__(self, file_id):
        self.file_id = file_id


class FakeMsg:
    def __init__(self, text=None, photo=None, caption=None, chat=None,
                 user=None):
        self.text = text
        self.photo = photo or []
        self.video = None
        self.document = None
        self.caption = caption
        self.chat = chat or FakeChat()
        self.from_user = user or FakeUser(1)
        self.replies = []
        self.chat_id = self.chat.id

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        self.reply_kws.append(kw)
        return self

    reply_kws = None  # di-set per instance di bawah

    def __init__2__(self):
        pass


# Perbaiki: reply_kws per instance
_orig_init = FakeMsg.__init__
def _new_init(self, text=None, photo=None, caption=None, chat=None, user=None):
    _orig_init(self, text=text, photo=photo, caption=caption, chat=chat,
               user=user)
    self.reply_kws = []
FakeMsg.__init__ = _new_init


class FakeUpdate:
    def __init__(self, msg):
        self.message = msg
        self.effective_user = msg.from_user
        self.effective_chat = msg.chat


class FakeQ:
    """Callback query palsu."""
    def __init__(self, data, user_id, msg):
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = msg
        self.answered = False

    async def answer(self, *a, **k):
        self.answered = True


class FakeQU:
    def __init__(self, q):
        self.callback_query = q
        self.effective_user = q.from_user


class FakeBot:
    def __init__(self):
        self.invoices = []
        self.sent = []  # (chat_id, kind)

    async def send_invoice(self, chat_id, title, description, payload,
                           provider_token, currency, prices):
        self.invoices.append({
            "chat_id": chat_id, "payload": payload,
            "stars": prices[0].amount,
        })
        return True

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, "text"))
        m = FakeMsg()
        m.message_id = 1000 + len(self.sent)
        return m

    async def send_photo(self, chat_id, photo, caption=None, **kw):
        self.sent.append((chat_id, "photo"))
        m = FakeMsg()
        m.message_id = 1000 + len(self.sent)
        return m


class FakeCtx:
    def __init__(self, conn, bot, admin_id=99, args=None):
        self.bot_data = {"db": conn, "admin_id": admin_id}
        self.bot = bot
        self.user_data = {}
        self.args = args or []


print("== jaseb ==")

# 1. /jaseb tanpa target → tolak halus
conn = db.init_db(":memory:")
bot = FakeBot()
msg = FakeMsg(chat=FakeChat(type="private"), user=FakeUser(10))
asyncio.run(jaseb.jaseb_cmd(FakeUpdate(msg), FakeCtx(conn, bot)))
_t("tanpa target: tolak halus",
   any("masih kosong" in r for r in msg.replies))

# 2. admin daftarkan 2 grup target
for gid, title in [(-100111, "Grup A"), (-100222, "Grup B")]:
    m = FakeMsg(chat=FakeChat(type="group", id=gid, title=title),
                user=FakeUser(99))
    asyncio.run(jaseb.jasebadd_cmd(FakeUpdate(m), FakeCtx(conn, bot)))
_t("jasebadd: 2 target terdaftar", len(jaseb._targets(conn)) == 2)

# 3. non-admin /jasebadd di grup → diabaikan (tanpa balas)
m3 = FakeMsg(chat=FakeChat(type="group", id=-100333, title="Grup C"),
             user=FakeUser(11))
asyncio.run(jaseb.jasebadd_cmd(FakeUpdate(m3), FakeCtx(conn, bot)))
_t("non-admin jasebadd: diabaikan",
   len(jaseb._targets(conn)) == 2 and m3.replies == [])

# 4. /jaseb → minta konten
ctx4 = FakeCtx(conn, bot)
m4 = FakeMsg(chat=FakeChat(type="private"), user=FakeUser(10))
asyncio.run(jaseb.jaseb_cmd(FakeUpdate(m4), ctx4))
_t("/jaseb: minta konten",
   any("Kirim konten" in r for r in m4.replies))
_t("/jaseb: flag menunggu diset", ctx4.user_data.get("jaseb_menunggu_konten"))

# 5. user kirim teks → draft + tampilkan paket
m5 = FakeMsg(text="PROMO DISKON 50%!", chat=FakeChat(type="private"),
             user=FakeUser(10))
asyncio.run(jaseb.terima_konten(FakeUpdate(m5), ctx4))
order = conn.execute(
    "SELECT id, content_type, content, status FROM jaseb_orders"
).fetchone()
_t("konten teks: draft dibuat",
   order and order[1] == "text" and order[2] == "PROMO DISKON 50%!"
   and order[3] == "draft")
_t("konten teks: tampilkan paket",
   any("Pilih paket" in r for r in m5.replies))
kb = m5.reply_kws[-1].get("reply_markup")
btns = [b.text for row in kb.inline_keyboard for b in row]
_t("paket: ada opsi 5 grup",
   any("5 grup" in b for b in btns) is False)  # cuma 2 target
_t("paket: ada opsi Semua (2 grup)",
   any("Semua (2 grup)" in b for b in btns))

# 6. pilih paket "semua" → invoice Stars keluar
order_id = order[0]
qm = FakeMsg(chat=FakeChat(type="private"), user=FakeUser(10))
q = FakeQ(f"jaseb_paket:{order_id}:2", 10, qm)
asyncio.run(jaseb.paket_cb(FakeQU(q), ctx4))
_t("paket dipilih: invoice terkirim", len(bot.invoices) == 1)
inv = bot.invoices[0]
_t("invoice: payload jaseb", inv["payload"] == f"jaseb:{order_id}:10")
_t("invoice: 2 grup x 10⭐ = 20", inv["stars"] == 20)
st = conn.execute(
    "SELECT status, target_count, price_stars FROM jaseb_orders WHERE id=?",
    (order_id,)).fetchone()
_t("order: awaiting_payment", st[0] == "awaiting_payment")
_t("order: 2 grup 20⭐", st[1] == 2 and st[2] == 20)
n_del = conn.execute(
    "SELECT COUNT(*) FROM jaseb_deliveries WHERE order_id=?", (order_id,)
).fetchone()[0]
_t("deliveries: 2 baris dibuat", n_del == 2)

# 7. precheckout valid & invalid
class FakePCQ:
    def __init__(self, payload, uid):
        self.invoice_payload = payload
        self.from_user = FakeUser(uid)
        self.ok = None
    async def answer(self, ok, error_message=None):
        self.ok = ok

class FakePCU:
    def __init__(self, q):
        self.pre_checkout_query = q

pcq_ok = FakePCQ(f"jaseb:{order_id}:10", 10)
asyncio.run(jaseb.precheckout(FakePCU(pcq_ok), ctx4))
_t("precheckout valid: ok", pcq_ok.ok is True)
pcq_bad = FakePCQ(f"jaseb:{order_id}:10", 11)  # payload user 10, yang bayar 11
asyncio.run(jaseb.precheckout(FakePCU(pcq_bad), ctx4))
_t("precheckout user beda: tolak", pcq_bad.ok is False)

# 8. _link_bukti: grup privat & publik
_t("link grup privat",
   jaseb._link_bukti(-100111, "", 55) == "https://t.me/c/111/55")
_t("link grup publik",
   jaseb._link_bukti(-100222, "grupb", 66) == "https://t.me/grupb/66")

# 9. /jasetarif
ctx9 = FakeCtx(conn, bot, args=["25"])
m9 = FakeMsg(user=FakeUser(99))
asyncio.run(jaseb.jasetarif_cmd(FakeUpdate(m9), ctx9))
_t("jasetarif: jadi 25",
   conn.execute("SELECT value FROM jaseb_config WHERE key='price_per_grup'"
                ).fetchone()[0] == "25")
_t("jasetarif: konfirmasi", any("25" in r for r in m9.replies))

# 10. /jasebadmin (admin)
m10 = FakeMsg(chat=FakeChat(type="private"), user=FakeUser(99))
asyncio.run(jaseb.jasebadmin_cmd(FakeUpdate(m10), FakeCtx(conn, bot)))
_t("jasebadmin: tampil target",
   any("Grup A" in r for r in m10.replies))
_t("jasebadmin: tampil tarif baru",
   any("25" in r for r in m10.replies))

# 11. /jasebadmin non-admin ditolak
m11 = FakeMsg(chat=FakeChat(type="private"), user=FakeUser(10))
asyncio.run(jaseb.jasebadmin_cmd(FakeUpdate(m11), FakeCtx(conn, bot)))
_t("jasebadmin non-admin: ditolak",
   any("Khusus admin" in r for r in m11.replies))

# 12. /jasebdel hapus grup
m12 = FakeMsg(chat=FakeChat(type="group", id=-100111, title="Grup A"),
              user=FakeUser(99))
asyncio.run(jaseb.jasebdel_cmd(FakeUpdate(m12), FakeCtx(conn, bot)))
_t("jasebdel: sisa 1 target", len(jaseb._targets(conn)) == 1)

# 13. /batal bersihkan draft
ctx13 = FakeCtx(conn, bot)
m13a = FakeMsg(chat=FakeChat(type="private"), user=FakeUser(10))
asyncio.run(jaseb.jaseb_cmd(FakeUpdate(m13a), ctx13))
m13b = FakeMsg(text="draft", chat=FakeChat(type="private"),
               user=FakeUser(10))
asyncio.run(jaseb.terima_konten(FakeUpdate(m13b), ctx13))
m13c = FakeMsg(chat=FakeChat(type="private"), user=FakeUser(10))
asyncio.run(jaseb.batal_cmd(FakeUpdate(m13c), ctx13))
sisa = conn.execute(
    "SELECT COUNT(*) FROM jaseb_orders WHERE user_id=10 AND status='draft'"
).fetchone()[0]
_t("/batal: draft dibersihkan", sisa == 0)

print("JASEB " + ("OK" if not fails else "GAGAL: %s" % fails))
sys.exit(1 if fails else 0)
