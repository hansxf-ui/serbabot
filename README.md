# SerbaBot

Bot Telegram multi-fitur: anon chat, downloader, tools foto/video, AI chat,
koin & daily check-in, kuis, game grup, primbon, jadwal sholat, dan banyak lagi.
Lihat daftar lengkap: kirim `/help` ke bot.

## Daftar fitur & perintah

| Plugin | Perintah | Jatah gratis |
|---|---|---|
| koin | `/checkin` (koin harian + streak 🔥), `/koin` (saldo & riwayat) | tanpa batas |
| kuis | `/kuis` (kuis harian, jawaban benar +5 koin), `/top` (leaderboard) | 3/hari |
| kuisai | `/kuisai <topik>` (kuis bikinan AI, jawaban benar +5 koin) | 2/hari |
| tod | `/tod` (truth or dare, grup & private) | 10/hari |
| menfess | `/menfess <teks>` (kirim anonim ke channel) | 3/hari |
| referral | `/invite` (link referral: L1 +100 koin, L2 +20 koin, 5 teman = 7 hari Premium) | — |
| aichat | `/ai <tanya>` | 5/hari |
| persona | `/persona` (pilih: Kak Curhat, Tutor Santai, Bang Roleplay, Mbah Primbon — AI ingat nama & fakta user) | 20/hari |
| rangkum | `/rangkum <url>` (ringkas artikel jadi 5 poin) | 5/hari |
| voice | `/vn <teks>` (teks → voice note id), kirim VN → transkrip teks | 3/hari (gabung) |
| primbon | `/zodiak <tgl>`, `/weton <tgl>` (Jumat Legi dst.), `/jodoh <tgl1> <tgl2>` | 30/hari |
| sholat | `/sholat <kota>`, `/sholatset <kota>` (pengingat 5 mnt sebelum), `/sholatstop` | 20/hari |
| skor | `/skor`, `/tebak <id> <skor>` (10 koin, tepat = +50 koin; BUKAN judi) | dibatasi koin |
| gacha | `/gacha` (1 gratis/hari), `/gachaplus` (50 koin/pull), `/koleksi` | 1 gratis/hari |
| meme | `/meme` (reply foto + `atas\|bawah`), `/stiker` (reply foto → stiker) | 10/hari & 5/hari |
| fotofun | `/memein` (reply foto → template lucu, tanpa face detection) | 20 koin/generate |
| grup | welcome member, anti-spam kata kasar/link, `/statistik` | — |
| resi | `/resi <nomor>` (butuh API key agregator) | 3/hari |
| paidmedia | `/katalog` (konten eksklusif via Stars) | dibatasi Stars |
| premium | `/premium` (50 ⭐ ±Rp12rb / 100 ⭐ ±Rp23rb per 30 hari, via Telegram Stars) | — |
| downloader | kirim link TikTok/IG/YouTube, `/dl <link>` | 5/hari |
| tools | kirim foto/video (PDF, convert, kompres, hapus background, potong) | 5/hari |
| imagegen | `/gambar <deskripsi>` | 3/hari |

VIP (admin atau premium aktif) = semua jatah unlimited, kecuali yang
sengaja dibatasi koin (skor, gachaplus, memein — itu desain ekonomi koin).

## Catatan deploy per fitur

- **menfess**: set `MENFESS_CHANNEL_ID` (ID channel, mis. `-100123...`). Tanpa
  ini `/menfess` menolak dengan jujur.
- **skor**: `FOOTBALL_API_KEY` opsional (football-data.org). Tanpa ini,
  settlement manual via `/addmatch` & `/setresult` (admin).
- **resi**: `RESI_PROVIDER` (`binderbyte` default / `custom`), `RESI_API_KEY`,
  `RESI_API_URL` (wajib kalau provider `custom`). Tanpa API key, `/resi`
  balas jujur "butuh API key agregator". Tidak ada API cek resi gratis tanpa
  key yang andal — ini batasan yang diketahui.
- **voice**: butuh `pip install edge-tts` (sudah di requirements.txt). Tanpa
  modulnya, `/vn` balas "belum aktif di server" tapi bot tetap jalan.
- **grup**: bot harus jadi **admin grup** (izin hapus pesan) + **privacy mode
  OFF** (BotFather → `/setprivacy` → Disable) supaya baca pesan grup.
- **sholat**: langganan tersimpan di DB dan di-restore otomatis saat bot
  restart (job dijadwalkan ulang di `register()`).
- **premium**: bayar via Telegram Stars (`sendInvoice` currency XTR) —
  satu-satunya cara bayar digital yang diizinkan Telegram untuk bot.
  Tidak ada lagi jalur QRIS/DANA manual.

## Plugin downloader (lama)

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
