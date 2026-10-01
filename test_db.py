"""Test db.is_vip: admin & premium bebas jatah, user biasa tidak."""
import sqlite3

from db import init_db, is_premium, is_vip, upsert_user

_pass = 0
_fail = 0


def _t(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS {name}")
    else:
        _fail += 1
        print(f"  FAIL {name}")


conn = init_db(":memory:")
ADMIN = 999
USER = 111
PREM = 222

upsert_user(conn, ADMIN, "admin")
upsert_user(conn, USER, "user")
upsert_user(conn, PREM, "prem")
conn.execute("UPDATE users SET is_premium=1 WHERE user_id=?", (PREM,))
conn.commit()

_t("admin bebas jatah (bukan premium di DB)", is_vip(conn, ADMIN, ADMIN))
_t("admin tetap vip walau id beda user", not is_vip(conn, USER, ADMIN))
_t("user premium vip", is_vip(conn, PREM, ADMIN))
_t("user biasa bukan vip", not is_vip(conn, USER, ADMIN))
_t("admin_id None: premium tetap vip", is_vip(conn, PREM, None))
_t("admin_id None: user biasa bukan vip", not is_vip(conn, USER, None))
_t("is_premium lama tidak berubah (admin bukan premium)", not is_premium(conn, ADMIN))
_t("is_premium lama tidak berubah (prem ya)", is_premium(conn, PREM))

print(f"\n{(_fail == 0 and 'DB OK') or 'DB GAGAL'} ({_pass} pass, {_fail} fail)")
raise SystemExit(1 if _fail else 0)
