# SerbaBot

Bot Telegram multi-fitur. Phase 1: anon-chat (ngobrol anonim 1 lawan 1).

## Struktur

```
serbabot/
├── bot.py                  # core: config, DB, load plugins, polling
├── db.py                   # SQLite helpers (stdlib)
├── plugins/
│   ├── __init__.py
│   └── anonchat/           # plugin phase 1
│       ├── __init__.py     # register(app, db): daftarkan handler
│       ├── pairing.py      # logika antrean/pairing (murni, testable)
│       ├── filtering.py    # filter kata kasar (stdlib)
│       └── badwords.txt    # daftar kata (satu per baris, bisa ditambah)
├── requirements.txt
├── serbabot.service        # systemd unit buat VPS
├── INSTALL.md              # panduan install di VPS
└── test_pairing.py         # unit test tanpa network
```

## Jalanin lokal

```bash
cd serbabot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

export BOT_TOKEN='token-dari-BotFather'
export ADMIN_ID='id-telegram-kamu'
./venv/bin/python bot.py
```

Dapetin `ADMIN_ID`: chat ke `@userinfobot` di Telegram, dia bales ID kamu.

Test logika (tanpa network, tanpa token):

```bash
python3 test_pairing.py
```

## Cara nambah plugin baru

Bikin modul di `plugins/`, misal `plugins/downloader.py`, dengan fungsi:

```python
def register(app, db):
    app.add_handler(CommandHandler("dl", download_cmd))
```

Core otomatis load semua modul di `plugins/` yang punya `register(app, db)`.
`db` = koneksi sqlite3 (tabel `users`, `reports` sudah ada). `app.bot_data["admin_id"]`
bisa dibaca kalau plugin butuh.

Contoh buat phase 2 (downloader): `plugins/downloader/__init__.py` + fungsi
`register` yang daftarkan `/dl <url>` → download → kirim file. Cek
`is_premium` user via `SELECT is_premium FROM users WHERE user_id=?` buat batasi
fitur gratis vs premium.

## Keputusan desain

- **Polling, bukan webhook.** Nggak butuh domain/HTTPS publik. Cukup jalan di VPS.
- **Phase 1 teks-only (tanpa foto/stiker/file).** Keputusan moderasi: media susah
  difilter otomatis, teks bisa kena badwords filter. Media dibuka di phase
  berikutnya bareng moderasi yang lebih siap.
- **SQLite via stdlib.** Satu file, nol setup. Cukup sampai puluhan ribu user.
  Kalau nanti kurang, ganti `db.py` doang — plugin nggak perlu diubah.
- **State pairing di memori.** Restart bot = sesi putus. Disengaja (YAGNI):
  sesi anon-chat memang ephemeral. Yang persisten (user, laporan) di SQLite.
- **Filter kata naive.** Cek kata utuh, bukan variasi (4nj1ng lolos). Cukup buat
  phase 1; daftar di `badwords.txt` gampang ditambah.
- **Token 100% via environment.** Tidak ada token di kode, di file, di repo.
