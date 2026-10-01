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
> dan `rembg` (tools hapus background).
> `ffmpeg` opsional tapi disarankan — tanpa ffmpeg, yt-dlp kadang nggak bisa
> gabung video+audio kualitas tertinggi. Install kalau mau:
> `sudo apt install -y ffmpeg`
>
> Catatan `rembg`: dipakai sebagai fallback kalau API mati. Model AI ~176MB
> diunduh otomatis saat pertama dipakai (butuh internet, agak lama).
> Proses di CPU.

## 2. Isi token & admin ID

```bash
sudo nano /etc/serbabot.env
```

Isi file-nya (ganti dengan punyamu):

```
BOT_TOKEN=xxxx_dari_BotFather
ADMIN_ID=123456789
REPLICATE_API_TOKEN=r8_xxxxxxxxxxxxxxxx
```

`REPLICATE_API_TOKEN` = buat hapus background yang cepat (~4 detik, GPU).
Daftar gratis di https://replicate.com (dapat $25 kredit, tanpa kartu kredit),
lalu ambil token di https://replicate.com/account/api-tokens.
Kalau dikosongkan, bot pakai rembg lokal (gratis tapi lambat, hitungan menit).

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
