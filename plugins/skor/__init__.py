"""Plugin Skor: tebak skor bola pakai koin virtual.

/skor                    — daftar match terbuka + leaderboard mini
/tebak <id> <skor>      — pasang tebakan (10 koin), contoh: /tebak 3 2-1
/addmatch (admin)       — tambah match: /addmatch Indonesia vs Malaysia 2026-10-05 19:00
/setresult <id> <skor> (admin) — set skor akhir + bagi hadiah

Tebakan tepat = +50 koin. Salah = hangus. Settlement otomatis tiap jam
kalau env FOOTBALL_API_KEY di-set, kalau nggak ya manual via /setresult.
"""
import datetime
import json
import logging
import os
import re
import urllib.request

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import add_coins, spend_coins, upsert_user

log = logging.getLogger("serbabot.skor")

TEBAK_COST = 10
TEBAK_WIN = 50
API_URL = "https://api.football-data.org/v4/matches?status=FINISHED"
API_TIMEOUT = 15

SCHEMA = """
CREATE TABLE IF NOT EXISTS skor_matches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    home TEXT NOT NULL,
    away TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    home_score INTEGER,
    away_score INTEGER
);
CREATE TABLE IF NOT EXISTS skor_tebak(
    match_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    home_pred INTEGER NOT NULL,
    away_pred INTEGER NOT NULL,
    won INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(match_id, user_id)
);
"""

DISCLAIMER = (
    "🎲 Tebak-tebakan seru pakai koin virtual. "
    "BUKAN judi — hadiah cuma koin, nggak ada uang asli."
)

_SCORE_RE = re.compile(r"^(\d+)-(\d+)$")
_ADDMATCH_RE = re.compile(
    r"^(.+?)\s+vs\s+(.+?)\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})$",
    re.IGNORECASE,
)


def parse_score(text):
    """'2-1' -> (2, 1). Format lain -> None."""
    m = _SCORE_RE.match((text or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_addmatch(text):
    """'<home> vs <away> YYYY-MM-DD HH:MM' -> (home, away, kickoff) | None."""
    m = _ADDMATCH_RE.match((text or "").strip())
    if not m:
        return None
    home, away, date, jam = (g.strip() for g in m.groups())
    try:
        kickoff = datetime.datetime.strptime(
            f"{date} {jam}", "%Y-%m-%d %H:%M"
        ).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return None
    if not home or not away:
        return None
    return home, away, kickoff


def add_match(conn, home, away, kickoff):
    cur = conn.execute(
        "INSERT INTO skor_matches(home, away, kickoff) VALUES(?,?,?)",
        (home, away, kickoff),
    )
    conn.commit()
    return cur.lastrowid


def get_match(conn, match_id):
    return conn.execute(
        "SELECT id, home, away, kickoff, status, home_score, away_score"
        " FROM skor_matches WHERE id=?",
        (match_id,),
    ).fetchone()


def get_open_matches(conn):
    return conn.execute(
        "SELECT id, home, away, kickoff FROM skor_matches"
        " WHERE status='open' ORDER BY kickoff"
    ).fetchall()


def place_bet(conn, user_id, match_id, home_pred, away_pred):
    """Return (ok, pesan). Koin dipotong kalau ok."""
    m = get_match(conn, match_id)
    if not m or m[4] != "open":
        return False, "Match-nya nggak ada atau udah ditutup 😅"
    dupe = conn.execute(
        "SELECT 1 FROM skor_tebak WHERE match_id=? AND user_id=?",
        (match_id, user_id),
    ).fetchone()
    if dupe:
        return False, "Lu udah pasang tebakan buat match ini. Satu aja per match!"
    if not spend_coins(conn, user_id, TEBAK_COST, f"tebak skor #{match_id}"):
        return False, "Koin lu kurang. /checkin dulu gih 🪙"
    conn.execute(
        "INSERT INTO skor_tebak(match_id, user_id, home_pred, away_pred)"
        " VALUES(?,?,?,?)",
        (match_id, user_id, home_pred, away_pred),
    )
    conn.commit()
    return True, (
        f"Tebakan masuk! {m[1]} vs {m[2]} → {home_pred}-{away_pred} 🎯\n"
        f"Tepat = +{TEBAK_WIN} koin. Salah = hangus."
    )


def settle_match(conn, match_id):
    """Bagi hadiah buat tebakan tepat. Return list user_id pemenang."""
    m = get_match(conn, match_id)
    if not m or m[4] != "done":
        return []
    h, a = m[5], m[6]
    winners = []
    for user_id, hp, ap in conn.execute(
        "SELECT user_id, home_pred, away_pred FROM skor_tebak"
        " WHERE match_id=? AND won=0",
        (match_id,),
    ).fetchall():
        if hp == h and ap == a:
            add_coins(conn, user_id, TEBAK_WIN, f"tebak skor tepat #{match_id}")
            winners.append(user_id)
    if winners:
        conn.execute(
            "UPDATE skor_tebak SET won=1 WHERE match_id=? AND user_id IN (%s)"
            % ",".join("?" * len(winners)),
            (match_id, *winners),
        )
        conn.commit()
    return winners


def set_result(conn, match_id, home_score, away_score):
    """Set skor akhir + settle. Return (ok, winners)."""
    m = get_match(conn, match_id)
    if not m:
        return False, []
    conn.execute(
        "UPDATE skor_matches SET status='done', home_score=?, away_score=?"
        " WHERE id=?",
        (home_score, away_score, match_id),
    )
    conn.commit()
    return True, settle_match(conn, match_id)


def get_leaderboard(conn, limit=5):
    """[(user_id, username, menang)] — tebakan tepat terbanyak."""
    return conn.execute(
        "SELECT t.user_id, u.username, COUNT(*) FROM skor_tebak t"
        " LEFT JOIN users u ON u.user_id=t.user_id"
        " WHERE t.won=1 GROUP BY t.user_id"
        " ORDER BY COUNT(*) DESC LIMIT ?",
        (limit,),
    ).fetchall()


# ---------- football-data.org (opsional) ----------

def fetch_finished(api_key):
    """Blocking: ambil match FINISHED dari football-data.org.

    Return [(home, away, home_score, away_score)]. Dipanggil via to_thread.
    """
    req = urllib.request.Request(API_URL, method="GET")
    req.add_header("X-Auth-Token", api_key)
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))
    out = []
    for m in payload.get("matches") or []:
        ft = ((m.get("score") or {}).get("fullTime")) or {}
        hs, aws = ft.get("home"), ft.get("away")
        if hs is None or aws is None:
            continue
        home = (m.get("homeTeam") or {}).get("shortName") or (
            m.get("homeTeam") or {}
        ).get("name") or "?"
        away = (m.get("awayTeam") or {}).get("shortName") or (
            m.get("awayTeam") or {}
        ).get("name") or "?"
        out.append((home, away, hs, aws))
    return out


def auto_settle(conn, api_key, now=None):
    """Cocokkan match open yang kickoff-nya udah lewat dengan hasil API.

    Return [match_id] yang ke-settle.
    """
    now = now or datetime.datetime.now().replace(microsecond=0)
    try:
        finished = fetch_finished(api_key)
    except Exception as e:  # noqa: BLE001 - API mati = skip ronde ini
        log.warning("skor auto-settle gagal fetch: %s", e)
        return []
    done = []
    for mid, home, away, kickoff in conn.execute(
        "SELECT id, home, away, kickoff FROM skor_matches WHERE status='open'"
    ).fetchall():
        try:
            ko = datetime.datetime.strptime(kickoff, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if ko >= now:
            continue
        h, a = home.lower(), away.lower()
        hit = next(
            (f for f in finished
             if h in f[0].lower() and a in f[1].lower()),
            None,
        )
        if not hit:
            continue
        ok, _ = set_result(conn, mid, hit[2], hit[3])
        if ok:
            done.append(mid)
            log.info("skor: match #%d auto-settled %s", mid, hit[2:])
    return done


# ---------- handlers ----------

def _is_admin(context, user_id):
    return context.bot_data.get("admin_id") == user_id


async def skor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    lines = ["⚽ Tebak Skor Bola\n"]
    open_ = get_open_matches(conn)
    if open_:
        for mid, home, away, kickoff in open_:
            lines.append(f"#{mid}  {home} vs {away}\n      ⏰ {kickoff}")
        lines.append("")
        lines.append("Pasang: /tebak <id> <skor>  (contoh: /tebak 3 2-1)")
        lines.append(f"Taruhan {TEBAK_COST} koin, tepat = +{TEBAK_WIN} koin 🪙")
    else:
        lines.append("Belum ada match. Admin bisa tambah via /addmatch.")

    lb = get_leaderboard(conn)
    if lb:
        lines.append("\n🏆 Paling jago nebak:")
        for i, (uid, uname, menang) in enumerate(lb, 1):
            lines.append(f"{i}. @{uname or uid} — {menang}x tepat")
    lines.append("")
    lines.append(DISCLAIMER)
    await update.message.reply_text("\n".join(lines))


async def tebak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.message.reply_text(
            "Pakai: /tebak <id> <skor>\nContoh: /tebak 3 2-1\n"
            "Lihat daftar match: /skor"
        )
        return
    pred = parse_score(context.args[1])
    if not pred:
        await update.message.reply_text(
            "Format skornya salah 😅 Contoh yang bener: 2-1\nCoba: /tebak 3 2-1"
        )
        return
    ok, msg = place_bet(conn, user.id, int(context.args[0]), *pred)
    await update.message.reply_text(msg)


async def addmatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    if not _is_admin(context, user.id):
        await update.message.reply_text(
            "Hehe, cuma admin yang bisa nambah match 🙏\n"
            "Mau nebak aja? Cek /skor"
        )
        return
    parsed = parse_addmatch(" ".join(context.args))
    if not parsed:
        await update.message.reply_text(
            "Pakai: /addmatch <home> vs <away> <YYYY-MM-DD> <HH:MM>\n"
            "Contoh: /addmatch Indonesia vs Malaysia 2026-10-05 19:00"
        )
        return
    home, away, kickoff = parsed
    mid = add_match(conn, home, away, kickoff)
    await update.message.reply_text(
        f"Match #{mid} ditambahin ✅\n{home} vs {away}\n⏰ {kickoff}"
    )


async def setresult_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)

    if not _is_admin(context, user.id):
        await update.message.reply_text(
            "Hehe, cuma admin yang bisa set hasil 🙏"
        )
        return
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.message.reply_text(
            "Pakai: /setresult <id> <skor>\nContoh: /setresult 3 2-1"
        )
        return
    skor = parse_score(context.args[1])
    if not skor:
        await update.message.reply_text(
            "Format skornya salah 😅 Contoh yang bener: 2-1"
        )
        return
    ok, winners = set_result(conn, int(context.args[0]), *skor)
    if not ok:
        await update.message.reply_text("Match-nya nggak ada 😅")
        return
    if winners:
        await update.message.reply_text(
            f"Skor akhir dicatat: {skor[0]}-{skor[1]} ✅\n"
            f"🎉 {len(winners)} pemenang, masing-masing +{TEBAK_WIN} koin!"
        )
    else:
        await update.message.reply_text(
            f"Skor akhir dicatat: {skor[0]}-{skor[1]} ✅\n"
            "Sayang, nggak ada yang nebak tepat 😅"
        )


async def _auto_job(context: ContextTypes.DEFAULT_TYPE):
    import asyncio

    api_key = os.environ.get("FOOTBALL_API_KEY")
    if not api_key:
        return
    conn = context.bot_data["db"]
    done = await asyncio.to_thread(auto_settle, conn, api_key)
    if done:
        log.info("skor auto-settle: %s", done)


def register(app: Application, db):
    db.executescript(SCHEMA)
    db.commit()
    app.add_handler(CommandHandler("skor", skor_cmd))
    app.add_handler(CommandHandler("tebak", tebak_cmd))
    app.add_handler(CommandHandler("addmatch", addmatch_cmd))
    app.add_handler(CommandHandler("setresult", setresult_cmd))
    if os.environ.get("FOOTBALL_API_KEY") and app.job_queue:
        app.job_queue.run_repeating(
            _auto_job, interval=3600, first=300, name="skor_auto_settle"
        )
        log.info("skor auto-settle dijadwalkan tiap 1 jam")
    log.info("skor plugin registered")
