# SerbaBot

Bot Telegram multi-fitur. Phase 1: anon-chat (ngobrol anonim 1 lawan 1).
Phase 2: downloader video (TikTok/IG Reels/YouTube/X/Facebook).

## Plugin downloader

Kirim link video langsung ke bot, atau pakai `/dl <link>`.

- Format otomatis `best[filesize<45M]/best` (limit file bot Telegram 50MB).
- File >45MB atau link gagal → pesan ramah Bahasa Indonesia, bukan error mentah.
- **Freemium:** user gratis = 5 download/hari (dihitung per user per tanggal).
  Premium (`users.is_premium=1`) = unlimited. Cek sisa jatah: nggak ada command
  khusus — kalau habis, bot kasih tahu + arahin ke `/premium`.
- File sementara di `/tmp`, dihapus setelah terkirim.
- Tidak mengganggu sesi anonchat: kalau user lagi ngobrol anonim, link yang
  dikirim tetap diteruskan sebagai chat biasa (bukan di-download).

## Struktur

```
serbabot/
├── bot.py                  # core: config, DB, load plugins, polling
├── db.py                   # SQLite helpers (stdlib)
├── plugins/
│   ├── __init__.py
│   ├── anonchat/           # plugin phase 1
│   │   ├── __init__.py     # register(app, db): daftarkan handler
│   │   ├── pairing.py      # logika antrean/pairing (murni, testable)
│   │   ├── filtering.py    # filter kata kasar (stdlib)
│   │   └── badwords.txt    # daftar kata (satu per baris, bisa ditambah)
│   └── downloader/         # plugin phase 2
│       └── __init__.py     # register(app, db): /dl + deteksi URL, limit harian
├── requirements.txt        # python-telegram-bot + yt-dlp
├── serbabot.service        # systemd unit buat VPS
├── INSTALL.md              # panduan install di VPS
├── test_pairing.py         # unit test tanpa network
└── test_downloader.py      # unit test limit harian (tanpa network)
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
python3 test_downloader.py
```

## Cara nambah plugin baru

Bikin modul di `plugins/`, misal `plugins/tools/`, dengan fungsi:

```python
def register(app, db):
    app.add_handler(CommandHandler("ping", ping_cmd))
```

Core otomatis load semua modul di `plugins/` yang punya `register(app, db)`.
`db` = koneksi sqlite3 (tabel `users`, `reports`, `downloads` sudah ada).
`app.bot_data["admin_id"]` bisa dibaca kalau plugin butuh.

Contoh nyata: `plugins/downloader/__init__.py` (deteksi URL + `/dl`,
cek sesi anonchat via `plugins.anonchat.is_in_session`, batasi fitur
gratis vs premium via `db.is_premium` / `db.get_downloads_today`).

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
