"""Jaseb (jasa sebar) otomatis: user order → bayar Stars → bot sebar ke grup.

Alur user:
  /jaseb → kirim konten (teks/foto/video) → pilih paket (tombol) →
  bayar Stars → bot broadcast ke grup target satu per satu (ada jeda
  anti-spam) → user terima laporan + link bukti.

Alur admin:
  - Masukin bot ke grup target (jadi member biasa cukup, NGGAK perlu admin).
    Khusus channel: bot wajib jadi admin.
  - Lalu di grup itu: /jasebadd
  - /jasebdel di grup untuk hapus dari daftar
  - /jasebadmin: lihat daftar target, order, atur tarif
  - /jasetarif <stars>: ubah harga per grup (default 10)

Batasan jujur: bot hanya bisa posting ke grup/channel yang bot-nya sudah
jadi member/admin. Tidak bisa sebar ke grup acak.
"""

import asyncio
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from db import upsert_user

log = logging.getLogger("jaseb")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jaseb_targets(
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS jaseb_orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,        -- text|photo|video|document
    content TEXT NOT NULL,             -- teks / file_id
    caption TEXT NOT NULL DEFAULT '',
    target_count INTEGER NOT NULL DEFAULT 0,
    price_stars INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',  -- draft|awaiting_payment|paid|broadcasting|done|failed
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS jaseb_deliveries(
    order_id INTEGER NOT NULL,
    target_chat_id INTEGER NOT NULL,
    message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|ok|fail
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (order_id, target_chat_id)
);
CREATE TABLE IF NOT EXISTS jaseb_config(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_PRICE = 10      # Stars per grup
DEFAULT_DELAY = 45      # detik jeda antar grup (anti-spam)
PAKET_PILIHAN = (5, 10, 20)  # pilihan jumlah grup (selain "semua")


def _db(context):
    return context.bot_data["db"]


def _is_admin(context, user_id):
    return user_id == context.bot_data.get("admin_id")


def _get_config(conn, key, default):
    row = conn.execute(
        "SELECT value FROM jaseb_config WHERE key=?", (key,)
    ).fetchone()
    if not row:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _set_config(conn, key, value):
    conn.execute(
        "INSERT INTO jaseb_config(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def _targets(conn):
    return conn.execute(
        "SELECT chat_id, title, username FROM jaseb_targets ORDER BY added_at"
    ).fetchall()


# ------------------------------------------------------------------ user flow

async def jaseb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mulai order: minta user kirim konten."""
    user = update.effective_user
    conn = _db(context)
    upsert_user(conn, user.id, user.username)
    conn.executescript(SCHEMA)

    n_target = len(_targets(conn))
    if n_target == 0:
        await update.message.reply_text(
            "Jaseb belum buka order — daftar grup sebarnya masih kosong. "
            "Coba lagi nanti ya 🙏"
        )
        return

    # Batalkan draft lama yang menggantung
    conn.execute(
        "DELETE FROM jaseb_orders WHERE user_id=? AND status='draft'",
        (user.id,),
    )
    conn.commit()

    context.user_data["jaseb_menunggu_konten"] = True
    await update.message.reply_text(
        "📣 *Order Jaseb*\n\n"
        "Kirim konten yang mau disebar (teks / foto / video). "
        "Untuk foto/video boleh tambah caption.\n\n"
        "Ketik /batal buat batalin.",
        parse_mode="Markdown",
    )


async def batal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = _db(context)
    conn.execute(
        "DELETE FROM jaseb_orders WHERE user_id=? AND status='draft'",
        (update.effective_user.id,),
    )
    conn.commit()
    context.user_data.pop("jaseb_menunggu_konten", None)
    context.user_data.pop("jaseb_draft_id", None)
    await update.message.reply_text("Order dibatalkan.")


async def terima_konten(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tangkap konten saat user dalam mode order jaseb."""
    if not context.user_data.get("jaseb_menunggu_konten"):
        return
    # Hanya di private chat
    if update.effective_chat.type != "private":
        return

    msg = update.message
    user = update.effective_user
    conn = _db(context)

    if msg.text:
        ctype, content, caption = "text", msg.text, ""
    elif msg.photo:
        ctype = "photo"
        content = msg.photo[-1].file_id
        caption = msg.caption or ""
    elif msg.video:
        ctype = "video"
        content = msg.video.file_id
        caption = msg.caption or ""
    elif msg.document:
        ctype = "document"
        content = msg.document.file_id
        caption = msg.caption or ""
    else:
        await msg.reply_text(
            "Kontennya harus teks, foto, atau video ya. Kirim ulang 🙏"
        )
        return

    cur = conn.execute(
        "INSERT INTO jaseb_orders(user_id, content_type, content, caption)"
        " VALUES(?,?,?,?)",
        (user.id, ctype, content, caption),
    )
    order_id = cur.lastrowid
    conn.commit()
    context.user_data.pop("jaseb_menunggu_konten", None)
    context.user_data["jaseb_draft_id"] = order_id

    await _tampilkan_paket(update, context, order_id)


async def _tampilkan_paket(update_or_q, context, order_id):
    """Tampilkan pilihan paket jumlah grup + harga."""
    conn = _db(context)
    n_target = len(_targets(conn))
    price = _get_config(conn, "price_per_grup", DEFAULT_PRICE)

    tombol = []
    for n in PAKET_PILIHAN:
        if n <= n_target:
            tombol.append([
                InlineKeyboardButton(
                    f"📦 {n} grup — {n * price}⭐",
                    callback_data=f"jaseb_paket:{order_id}:{n}",
                )
            ])
    if n_target not in PAKET_PILIHAN:
        tombol.append([
            InlineKeyboardButton(
                f"🚀 Semua ({n_target} grup) — {n_target * price}⭐",
                callback_data=f"jaseb_paket:{order_id}:{n_target}",
            )
        ])

    teks = (
        "Konten diterima ✓\n\n"
        f"Grup tersedia: {n_target}\n"
        f"Tarif: {price}⭐ per grup\n\n"
        "Pilih paket sebar:"
    )
    kb = InlineKeyboardMarkup(tombol)

    if hasattr(update_or_q, "message") and hasattr(update_or_q, "data"):
        await update_or_q.message.reply_text(teks, reply_markup=kb)
    else:
        await update_or_q.message.reply_text(teks, reply_markup=kb)


async def paket_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User pilih paket → buat invoice Stars."""
    q = update.callback_query
    await q.answer()
    try:
        _, order_id_s, n_s = q.data.split(":")
        order_id, n_grup = int(order_id_s), int(n_s)
    except (ValueError, IndexError):
        await q.message.reply_text("Paket nggak dikenal. /jaseb lagi gih.")
        return

    conn = _db(context)
    order = conn.execute(
        "SELECT user_id, status FROM jaseb_orders WHERE id=?", (order_id,)
    ).fetchone()
    if not order or order[0] != q.from_user.id or order[1] != "draft":
        await q.message.reply_text(
            "Order-nya udah nggak valid. Mulai lagi: /jaseb"
        )
        return

    targets = _targets(conn)
    if n_grup > len(targets) or n_grup <= 0:
        await q.message.reply_text(
            "Jumlah grup nggak valid. /jaseb lagi gih."
        )
        return

    price = _get_config(conn, "price_per_grup", DEFAULT_PRICE)
    total = n_grup * price

    conn.execute(
        "UPDATE jaseb_orders SET target_count=?, price_stars=?,"
        " status='awaiting_payment' WHERE id=?",
        (n_grup, total, order_id),
    )
    # Simpan daftar target yang dipilih (urutan awal daftar)
    chosen = targets[:n_grup]
    conn.executemany(
        "INSERT OR IGNORE INTO jaseb_deliveries(order_id, target_chat_id)"
        " VALUES(?,?)",
        [(order_id, t[0]) for t in chosen],
    )
    conn.commit()
    context.user_data.pop("jaseb_draft_id", None)

    try:
        await context.bot.send_invoice(
            chat_id=q.message.chat_id,
            title="Jaseb — Sebar Konten",
            description=f"Sebar ke {n_grup} grup ({total} Stars).",
            payload=f"jaseb:{order_id}:{q.from_user.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(f"Jaseb {n_grup} grup", total)],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("jaseb send_invoice gagal: %s", e)
        conn.execute(
            "UPDATE jaseb_orders SET status='draft' WHERE id=?", (order_id,)
        )
        conn.commit()
        await q.message.reply_text(
            "Duh, gagal buka halaman bayar 😅 Coba /jaseb lagi."
        )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    parts = (q.invoice_payload or "").split(":")
    ok = (
        len(parts) == 3
        and parts[0] == "jaseb"
        and parts[2] == str(q.from_user.id)
    )
    if ok:
        # Pastikan order-nya memang menunggu bayar
        conn = _db(context)
        row = conn.execute(
            "SELECT status FROM jaseb_orders WHERE id=?", (parts[1],)
        ).fetchone()
        ok = bool(row and row[0] == "awaiting_payment")
    if ok:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Order nggak valid. /jaseb lagi gih.")


async def paid_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = _db(context)
    pay = update.message.successful_payment
    try:
        _, order_id_s, uid_s = pay.invoice_payload.split(":")
        order_id = int(order_id_s)
    except (ValueError, IndexError):
        return
    order = conn.execute(
        "SELECT user_id, status, target_count FROM jaseb_orders WHERE id=?",
        (order_id,),
    ).fetchone()
    if not order or order[1] != "awaiting_payment":
        return
    conn.execute(
        "UPDATE jaseb_orders SET status='paid' WHERE id=?", (order_id,)
    )
    conn.commit()
    log.info("jaseb: order %s dibayar user %s", order_id, order[0])
    await update.message.reply_text(
        "✅ Pembayaran diterima! Konten lu lagi disebar ke "
        f"{order[2]} grup...\n\n"
        "Bot kasih jeda antar grup biar aman dari limit spam, "
        "jadi tunggu ya. Nanti gue kirim laporannya."
    )
    # Jalanin broadcast di background
    asyncio.create_task(_broadcast(context, order_id))


# ------------------------------------------------------------------ broadcast

def _link_bukti(chat_id, username, message_id):
    if username:
        return f"https://t.me/{username}/{message_id}"
    # Grup privat: format t.me/c/<id tanpa -100>/<msg>
    cid = str(chat_id)
    if cid.startswith("-100"):
        cid = cid[4:]
    return f"https://t.me/c/{cid}/{message_id}"


async def _kirim_satu(bot, target_chat_id, order):
    """Kirim konten order ke satu grup. Return (ok, message_id, error)."""
    ctype, content, caption = order[0], order[1], order[2]
    try:
        if ctype == "text":
            m = await bot.send_message(chat_id=target_chat_id, text=content)
        elif ctype == "photo":
            m = await bot.send_photo(
                chat_id=target_chat_id, photo=content, caption=caption or None
            )
        elif ctype == "video":
            m = await bot.send_video(
                chat_id=target_chat_id, video=content, caption=caption or None
            )
        else:
            m = await bot.send_document(
                chat_id=target_chat_id, document=content,
                caption=caption or None,
            )
        return True, m.message_id, ""
    except Exception as e:  # noqa: BLE001
        return False, None, str(e)


async def _broadcast(context: ContextTypes.DEFAULT_TYPE, order_id: int):
    """Sebar konten order ke semua target dengan jeda anti-spam."""
    bot = context.bot
    # Buka koneksi DB baru khusus task background (thread-safe)
    import db as dbmod

    db_path = context.bot_data.get("db_path", "serbabot.db")
    conn = dbmod.init_db(db_path)
    try:
        conn.executescript(SCHEMA)
        order = conn.execute(
            "SELECT content_type, content, caption, user_id, target_count"
            " FROM jaseb_orders WHERE id=?",
            (order_id,),
        ).fetchone()
        if not order:
            return
        conn.execute(
            "UPDATE jaseb_orders SET status='broadcasting' WHERE id=?",
            (order_id,),
        )
        conn.commit()

        delay = _get_config(conn, "delay_detik", DEFAULT_DELAY)
        deliveries = conn.execute(
            "SELECT d.target_chat_id, t.username, t.title"
            " FROM jaseb_deliveries d"
            " JOIN jaseb_targets t ON t.chat_id=d.target_chat_id"
            " WHERE d.order_id=? AND d.status='pending'"
            " ORDER BY t.added_at",
            (order_id,),
        ).fetchall()

        ok_count = 0
        bukti = []
        for i, (chat_id, username, title) in enumerate(deliveries):
            if i > 0:
                await asyncio.sleep(delay)
            ok, mid, err = await _kirim_satu(
                bot, chat_id, (order[0], order[1], order[2])
            )
            if ok:
                ok_count += 1
                conn.execute(
                    "UPDATE jaseb_deliveries SET status='ok', message_id=?"
                    " WHERE order_id=? AND target_chat_id=?",
                    (mid, order_id, chat_id),
                )
                bukti.append(_link_bukti(chat_id, username, mid))
            else:
                log.warning("jaseb order %s gagal ke %s: %s",
                            order_id, chat_id, err)
                conn.execute(
                    "UPDATE jaseb_deliveries SET status='fail', error=?"
                    " WHERE order_id=? AND target_chat_id=?",
                    (err[:300], order_id, chat_id),
                )
            conn.commit()

        conn.execute(
            "UPDATE jaseb_orders SET status='done',"
            " completed_at=datetime('now') WHERE id=?",
            (order_id,),
        )
        conn.commit()

        # Laporan ke pemesan
        user_id = order[3]
        lines = [
            "📣 *Laporan Jaseb*",
            f"Order #{order_id}: {ok_count}/{len(deliveries)} grup berhasil.",
            "",
        ]
        for link in bukti[:30]:
            lines.append("• " + link)
        if len(bukti) > 30:
            lines.append(f"... +{len(bukti) - 30} lagi")
        if ok_count < len(deliveries):
            lines.append(
                f"\n{len(deliveries) - ok_count} grup gagal "
                "(mungkin bot dikeluarin/dibatasi)."
            )
        try:
            await bot.send_message(
                chat_id=user_id, text="\n".join(lines),
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("jaseb: gagal kirim laporan ke %s: %s", user_id, e)

        # Notif ke admin
        admin_id = context.bot_data.get("admin_id")
        if admin_id:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=f"📣 Jaseb order #{order_id} selesai: "
                         f"{ok_count}/{len(deliveries)} grup.",
                )
            except Exception:  # noqa: BLE001
                pass
    finally:
        conn.close()


# ------------------------------------------------------------------ admin

async def jasebadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Daftarkan grup ini sebagai target (jalanin di dalam grup)."""
    if not _is_admin(context, update.effective_user.id):
        return
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text(
            "Jalanin /jasebadd di dalam grup yang mau didaftarin, "
            "bukan di chat pribadi."
        )
        return
    conn = _db(context)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO jaseb_targets(chat_id, title, username)"
        " VALUES(?,?,?)",
        (chat.id, chat.title or "", chat.username or ""),
    )
    conn.commit()
    n = len(_targets(conn))
    await update.message.reply_text(
        f"✅ Grup '{chat.title}' masuk daftar sebar. Total: {n} grup."
    )


async def jasebdel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hapus grup ini dari daftar (jalanin di dalam grup)."""
    if not _is_admin(context, update.effective_user.id):
        return
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text(
            "Jalanin /jasebdel di dalam grup yang mau dihapus."
        )
        return
    conn = _db(context)
    conn.execute("DELETE FROM jaseb_targets WHERE chat_id=?", (chat.id,))
    conn.commit()
    await update.message.reply_text("🗑️ Grup dihapus dari daftar sebar.")


async def jasetarif_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jasetarif <stars> — ubah harga per grup."""
    if not _is_admin(context, update.effective_user.id):
        return
    conn = _db(context)
    conn.executescript(SCHEMA)
    if not context.args:
        cur = _get_config(conn, "price_per_grup", DEFAULT_PRICE)
        await update.message.reply_text(
            f"Tarif sekarang: {cur}⭐ per grup.\n"
            "Ubah: /jasetarif <angka>"
        )
        return
    try:
        val = int(context.args[0])
        if val < 1 or val > 10000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Angkanya 1–10000 ya.")
        return
    _set_config(conn, "price_per_grup", val)
    await update.message.reply_text(f"✅ Tarif jadi {val}⭐ per grup.")


async def jasebadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Panel admin: daftar target + order terakhir."""
    if not _is_admin(context, update.effective_user.id):
        await update.message.reply_text("Khusus admin.")
        return
    conn = _db(context)
    conn.executescript(SCHEMA)
    targets = _targets(conn)
    price = _get_config(conn, "price_per_grup", DEFAULT_PRICE)
    delay = _get_config(conn, "delay_detik", DEFAULT_DELAY)

    lines = ["📣 *Panel Jaseb*", f"Tarif: {price}⭐/grup • Jeda: {delay}s",
             f"Target: {len(targets)} grup", ""]
    for t in targets[:20]:
        uname = f" (@{t[2]})" if t[2] else ""
        lines.append(f"• {t[1]}{uname}")
    if len(targets) > 20:
        lines.append(f"... +{len(targets) - 20} lagi")

    orders = conn.execute(
        "SELECT id, user_id, target_count, price_stars, status, created_at"
        " FROM jaseb_orders ORDER BY id DESC LIMIT 5"
    ).fetchall()
    lines += ["", "*Order terakhir:*"]
    for o in orders:
        lines.append(f"#{o[0]} u{o[1]} {o[2]} grup {o[3]}⭐ [{o[4]}]")
    if not orders:
        lines.append("(belum ada)")

    lines += ["",
              "Perintah: /jasebadd & /jasebdel (di grup), /jasetarif <n>"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def register(app: Application, db):
    app.add_handler(CommandHandler("jaseb", jaseb_cmd))
    app.add_handler(CommandHandler("batal", batal_cmd))
    app.add_handler(CommandHandler("jasebadd", jasebadd_cmd))
    app.add_handler(CommandHandler("jasebdel", jasebdel_cmd))
    app.add_handler(CommandHandler("jasetarif", jasetarif_cmd))
    app.add_handler(CommandHandler("jasebadmin", jasebadmin_cmd))
    app.add_handler(CallbackQueryHandler(paket_cb, pattern=r"^jaseb_paket:"))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    # successful_payment khusus jaseb (payload diawali "jaseb:")
    app.add_handler(MessageHandler(
        filters.SUCCESSFUL_PAYMENT, _paid_router,
    ))
    # Tangkap konten order (harus di bawah handler lain biar nggak nyerobot)
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL)
        & filters.ChatType.PRIVATE,
        terima_konten,
    ), group=1)
    log.info("jaseb plugin registered")


async def _paid_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Arahkan successful_payment ke handler yang sesuai payload."""
    payload = (update.message.successful_payment.invoice_payload or "")
    if payload.startswith("jaseb:"):
        await paid_success(update, context)
    # payload "premium:..." ditangani plugin premium sendiri
