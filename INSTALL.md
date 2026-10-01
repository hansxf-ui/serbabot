# Install SerbaBot di VPS (Ubuntu 24.04)

Semua perintah di bawah dijalankan **di dalam VPS** — setelah `ssh tencent`,
prompt-nya `ubuntu@...` (bukan di Termux langsung).

## 1. Siapkan folder & venv

```bash
sudo apt update && sudo apt install -y python3-venv git
sudo mkdir -p /opt/serbabot
sudo chown ubuntu:ubuntu /opt/serbabot
cd /opt/serbabot
```

Copy semua file bot ke `/opt/serbabot` (via `git clone` atau `scp`), lalu:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

> Ubuntu 24.04 melarang `pip install` global (PEP 668). Makanya pakai venv
> seperti di atas — jangan pakai `sudo pip`.

> `requirements.txt` sudah mencakup `yt-dlp` (downloader), `pillow` (tools),
> dan `rembg[cpu]` (tools hapus background).
> `ffmpeg` **WAJIB** untuk tools video (kompres / convert MP4 / potong).
> Tanpa ffmpeg, tools video nonaktif sendiri (bot tetap jalan).
> Install di VPS:
> `sudo apt install -y ffmpeg`
>
> Catatan `rembg`: dipakai sebagai fallback kalau API mati. Model AI u2net
> ~176MB diunduh otomatis saat pertama dipakai (butuh internet, agak lama).
> Proses di CPU (hitungan detik per foto).

## 2. Isi token & admin ID

```bash
sudo nano /etc/serbabot.env
```

Isi file-nya (ganti dengan punyamu):

```
BOT_TOKEN=xxxx_dari_BotFather
ADMIN_ID=123456789
REPLICATE_API_TOKEN=r8_xxxxxxxxxxxxxxxx
YTDL_COOKIES=/opt/serbabot/cookies.txt
GEMINI_API_KEY=xxxx_dari_aistudio
MENFESS_CHANNEL_ID=-100xxxxxxxxxx
FOOTBALL_API_KEY=
RESI_PROVIDER=binderbyte
RESI_API_KEY=
RESI_API_URL=
```

`REPLICATE_API_TOKEN` = buat hapus background yang cepat (~4 detik, GPU).
Daftar gratis di https://replicate.com (dapat $25 kredit, tanpa kartu kredit),
lalu ambil token di https://replicate.com/account/api-tokens.
Kalau dikosongkan, bot pakai rembg lokal (gratis, ~1-2 detik/foto).

`YTDL_COOKIES` = opsional, path ke file cookies.txt buat downloader YouTube/
Instagram biar nggak kena blokir bot. Cara isi: buka youtube.com di browser
HP (Lemur Browser), export cookies pakai ekstensi "Get cookies.txt LOCALLY",
lalu upload file-nya ke VPS. Kalau dikosongkan, downloader tetap jalan tapi
kadang gagal.

`GEMINI_API_KEY` = bikin `/ai` jawab akurat (tanpa ini, `/ai` pakai
Pollinations gratis yang kadang ngawur). Dipakai juga oleh `/persona`,
`/kuisai`, `/rangkum`, dan transkrip voice note. Ambil gratis di
https://aistudio.google.com/apikey (kuota gratis 1500x/hari, tanpa kartu
kredit). Kalau dikosongkan, `/ai` tetap jalan tapi jawabannya bisa ngaco.

`MENFESS_CHANNEL_ID` = ID channel tujuan `/menfess` (mis. `-1001234567890`;
ambil via @userinfobot setelah forward pesan channel). Tanpa ini, `/menfess`
menolak dengan pesan jujur (tidak fake kirim).

`FOOTBALL_API_KEY` = opsional, dari https://www.football-data.org (tier gratis).
Kalau di-set, tebak skor otomatis settlement tiap jam. Kalau dikosongkan,
admin settlement manual via `/addmatch` & `/setresult` — fitur tetap jalan.

`RESI_PROVIDER` = `binderbyte` (default) atau `custom`.
`RESI_API_KEY` = API key agregator cek resi (BinderByte butuh daftar, freemium).
`RESI_API_URL` = wajib kalau provider `custom` (GET `<url>?awb=<nomor>`,
header `Authorization: Bearer <key>`).
Tanpa `RESI_API_KEY`, `/resi` balas jujur "butuh API key agregator" —
tidak ada API cek resi gratis tanpa key yang andal (RajaOngkir gratis cuma
cek ongkir, bukan tracking).

Fitur voice (`/vn`) butuh package `edge-tts` — sudah ada di
`requirements.txt`, jadi ke-install otomatis via `pip install -r`.
Tanpa itu `/vn` balas "belum aktif di server" tapi bot tetap jalan.

Fitur grup (welcome + anti-spam): jadikan bot **admin grup** (beri izin hapus
pesan) dan matikan **privacy mode** (chat @BotFather → `/setprivacy` →
pilih bot → Disable). Tanpa ini bot tidak bisa baca/hapus pesan grup.

Pembayaran Premium pakai **Telegram Stars** (invoice via bot, currency XTR) —
tidak butuh env tambahan. Satu-satunya cara bayar digital yang diizinkan
Telegram untuk bot. Jalur manual QRIS/DANA sudah dihapus.

Simpan: `Ctrl+O`, `Enter`, `Ctrl+X`. Lalu kunci permission-nya:

```bash
sudo chmod 600 /etc/serbabot.env
```

> `BOT_TOKEN` didapat dari `@BotFather` di Telegram (`/newbot`).
> `ADMIN_ID` = ID Telegram kamu — chat `@userinfobot`, dia bales ID-nya.
> **Jangan kirim token ke siapa-siapa, termasuk ke Vesper.**

## 3. Pasang service

```bash
sudo cp /opt/serbabot/serbabot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now serbabot
systemctl status serbabot
```

## 4. Cek log

```bash
journalctl -u serbabot -f
```

Buka Telegram, chat ke bot-mu, kirim `/start`. Kalau dibales, berarti jalan.

## Kalau update kode

```bash
cd /opt/serbabot
git pull
./venv/bin/pip install -r requirements.txt  # kalau ada dep baru (pillow, rembg, ...)
sudo systemctl restart serbabot
```

## Matikan bot

```bash
sudo systemctl stop serbabot
sudo systemctl disable serbabot
```
