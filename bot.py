#!/usr/bin/env python3
"""SerbaBot core: baca config, init DB, load plugins, start polling.

Core TIDAK tau soal fitur. Semua fitur tinggal di plugins/*/
"""
import importlib
import logging
import os
import pkgutil
import sys

from telegram.ext import ApplicationBuilder

import db
import plugins

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("serbabot")


def load_config():
    token = os.environ.get("BOT_TOKEN", "").strip()
    admin_raw = os.environ.get("ADMIN_ID", "").strip()
    if not token:
        sys.exit("BOT_TOKEN belum di-set. Pasang di environment dulu.")
    try:
        admin_id = int(admin_raw)
    except ValueError:
        sys.exit("ADMIN_ID harus angka (ID Telegram admin).")
    return token, admin_id


def load_plugins(app, conn):
    count = 0
    for _, name, _ in pkgutil.iter_modules(plugins.__path__):
        mod = importlib.import_module(f"plugins.{name}")
        register = getattr(mod, "register", None)
        if callable(register):
            register(app, conn)
            count += 1
            log.info("plugin loaded: %s", name)
    if count == 0:
        log.warning("tidak ada plugin yang ke-load!")


def main():
    token, admin_id = load_config()
    db_path = os.environ.get("DB_PATH", "serbabot.db")
    conn = db.init_db(db_path)
    app = ApplicationBuilder().token(token).build()
    app.bot_data["db"] = conn
    app.bot_data["admin_id"] = admin_id
    load_plugins(app, conn)
    log.info("SerbaBot jalan (polling).")
    app.run_polling()


if __name__ == "__main__":
    main()
