"""Plugin primbon: zodiak, weton Jawa, dan hitung-hitungan jodoh.

/zodiak <dd-mm-yyyy>  — zodiak + ramalan harian (varian ganti tiap hari)
/weton <dd-mm-yyyy>   — weton Jawa (hari + pasaran), neptu, watak
/jodoh <tgl1> <tgl2>  — kecocokan dari jumlah neptu

Murni Python, tanpa API. Cuma hiburan tradisi 😄
"""
import datetime
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from db import (
    get_quota_today,
    inc_quota_today,
    is_vip,
    upsert_user,
)

log = logging.getLogger("serbabot.primbon")

PRIMBON_LIMIT = 30  # per hari, longgar — biar nggak di-spam

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
PASARAN = ["Legi", "Pahing", "Pon", "Wage", "Kliwon"]

NEPTU_HARI = {
    "Senin": 4, "Selasa": 3, "Rabu": 7, "Kamis": 8,
    "Jumat": 6, "Sabtu": 9, "Minggu": 5,
}
NEPTU_PASARAN = {"Legi": 5, "Pahing": 9, "Pon": 7, "Wage": 4, "Kliwon": 8}

# Anchor yang jadi acuan: 17 Agustus 1945 = Jumat Legi.
ANCHOR = datetime.date(1945, 8, 17)

WATAK_HARI = {
    "Senin": "Orangnya rajin dan tekun, kalau udah niat jarang setengah-setengah.",
    "Selasa": "Tegas dan berani, tapi kadang gampang emosi kalau lagi capek.",
    "Rabu": "Cerdas dan pandai ngomong, gampang disukai orang.",
    "Kamis": "Pemikir yang tenang, cocok jadi tempat curhat.",
    "Jumat": "Ramah dan dermawan, rezekinya biasanya ngalir.",
    "Sabtu": "Pekerja keras yang pantang nyerah, walau kadang keras kepala.",
    "Minggu": "Berwibawa dan punya aura pemimpin, disegani banyak orang.",
}

WATAK_PASARAN = {
    "Legi": "Pembawaannya manis dan disukai, cocok bergaul sama siapa aja.",
    "Pahing": "Ambisius dan berani ambil risiko, tapi perlu sabar.",
    "Pon": "Cerdas dan pandai cari peluang, rezeki sering datang dari arah tak terduga.",
    "Wage": "Sabar dan setia kawan, temen yang bisa diandelin.",
    "Kliwon": "Punya intuisi tajam dan aura misterius, sering 'ngeh' duluan.",
}

# (nama, bulan_mulai, tanggal_mulai, rentang tampilan)
ZODIAK = [
    ("Capricorn", 12, 22, "22 Des – 20 Jan"),
    ("Aquarius", 1, 21, "21 Jan – 19 Feb"),
    ("Pisces", 2, 20, "20 Feb – 20 Mar"),
    ("Aries", 3, 21, "21 Mar – 20 Apr"),
    ("Taurus", 4, 21, "21 Apr – 20 Mei"),
    ("Gemini", 5, 21, "21 Mei – 20 Jun"),
    ("Cancer", 6, 21, "21 Jun – 22 Jul"),
    ("Leo", 7, 23, "23 Jul – 22 Agu"),
    ("Virgo", 8, 23, "23 Agu – 22 Sep"),
    ("Libra", 9, 23, "23 Sep – 22 Okt"),
    ("Scorpio", 10, 23, "23 Okt – 21 Nov"),
    ("Sagitarius", 11, 22, "22 Nov – 21 Des"),
]

# Template ramalan: [asmara, karier, kesehatan]
RAMALAN = {
    "Capricorn": [
        "Lagi pengen yang pasti-pasti aja. Kalau ada yang bikin nyaman, jangan gengsi buat nunjukin duluan.",
        "Kerja keras lu mulai dilirik atasan. Jangan kendor, tapi jangan juga lupa istirahat.",
        "Badan mulai protes karena kebanyakan duduk. Jalan bentar tiap sore, lumayan ngefek.",
    ],
    "Aquarius": [
        "Obrolan receh hari ini bisa berubah jadi deep talk. Manfaatin momennya.",
        "Ide nyeleneh lu ternyata masuk akal. Beraniin nyampein di forum.",
        "Kurang tidur bikin mood ancur. Malam ini usahain tidur lebih awal.",
    ],
    "Pisces": [
        "Perasaan lu lagi peka banget. Jangan langsung baper, cek dulu faktanya.",
        "Kreativitas lagi tinggi, cocok buat ngerjain yang butuh imajinasi.",
        "Jangan kebanyakan rebahan, badan jadi makin lemes. Gerak dikit aja.",
    ],
    "Aries": [
        "Jangan terlalu agresif ngejar. Kadang yang sabar malah menang.",
        "Energi lu lagi full, gas pol buat beresin tugas yang ketunda.",
        "Hati-hati kebanyakan begadang, kepala gampang pusing.",
    ],
    "Taurus": [
        "Kesetiaan lu lagi diuji. Tetep tenang, yang bener bakal keliatan.",
        "Keuangan lagi stabil, tapi jangan kalap belanja. Nabung dikit.",
        "Perut lagi sensitif, kurangin yang pedes-pedes dulu.",
    ],
    "Gemini": [
        "Chat-nya rame, tapi yang serius cuma satu. Lu tau siapa.",
        "Multitasking lagi jalan, tapi pastiin nggak ada yang kelewat.",
        "Otak overthinking bikin capek sendiri. Coba diem 5 menit tanpa HP.",
    ],
    "Cancer": [
        "Lagi kangen berat sama seseorang. Sapa duluan, nggak ada salahnya.",
        "Intuisi lu soal kerjaan lagi tajem. Percaya insting sendiri.",
        "Jaga pola makan, maag gampang kambuh kalau telat makan.",
    ],
    "Leo": [
        "Pesona lu lagi on fire. Ada yang diam-diam merhatiin.",
        "Saatnya nunjukin kemampuan. Jangan malu tampil beda.",
        "Stamina lagi bagus, cocok buat olahraga. Jangan dipaksa aja.",
    ],
    "Virgo": [
        "Jangan terlalu kritis ke pasangan. Kadang 'yaudah' itu jawaban terbaik.",
        "Detail kecil yang lu perhatiin bikin hasil kerja beda kelas.",
        "Pundak tegang karena stres. Stretching bentar tiap jam.",
    ],
    "Libra": [
        "Lagi bimbang milih. Ikutin kata hati, bukan kata orang.",
        "Kerja tim lagi harmonis. Jadi penengah itu kekuatan lu.",
        "Seimbangin kerja dan istirahat. Badan bukan mesin.",
    ],
    "Scorpio": [
        "Intensitas lu bikin dia deg-degan. Pelan-pelan aja.",
        "Fokus lu lagi tajem banget, cocok buat ngerjain yang rumit.",
        "Emosi kependem bikin badan pegel. Salurin lewat olahraga.",
    ],
    "Sagitarius": [
        "Petualangan bareng doi bisa bikin hubungan makin deket. Ajak jalan.",
        "Peluang baru muncul dari arah yang nggak disangka. Siap-siap.",
        "Kaki gatel pengen jalan-jalan. Olahraga outdoor cocok hari ini.",
    ],
}

ASPEK = ["Asmara", "Karier", "Kesehatan"]

JODOH_KATEGORI = ["Kurang cocok", "Lumayan", "Cocok", "Sangat cocok", "Jodoh sejati"]
JODOH_KALIMAT = {
    "Kurang cocok": "Kayaknya bakal sering beda pendapat — butuh sabar ekstra kalau mau lanjut.",
    "Lumayan": "Ada chemistry-nya, tinggal dipoles dikit biar makin klop.",
    "Cocok": "Kalian saling ngelengkapi, lanjutin aja yang bener.",
    "Sangat cocok": "Jarang ada pasangan se-klop ini. Dijaga baik-baik ya.",
    "Jodoh sejati": "Katanya sih ini paket komplit. Tinggal buktin di dunia nyata.",
}


# Urutan start menaik dalam setahun; Capricorn (22 Des) paling akhir
# karena rentangnya nyebrang tahun baru.
_ZODIAK_ORDER = ZODIAK[1:] + [ZODIAK[0]]


def zodiak_of(day, month):
    """(nama, rentang) zodiak dari tanggal lahir."""
    hasil = _ZODIAK_ORDER[-1]  # Capricorn: kalau tanggal < 21 Jan
    for z in _ZODIAK_ORDER:
        if (month, day) >= (z[1], z[2]):
            hasil = z
    return hasil[0], hasil[3]


def ramalan(nama_zodiak, day_of_year):
    """(aspek, teks) ramalan hari itu. Varian ganti tiap hari."""
    i = day_of_year % 3
    return ASPEK[i], RAMALAN[nama_zodiak][i]


def weton_of(d):
    """(hari, pasaran) dari tanggal. Anchor: 17-08-1945 = Jumat Legi."""
    delta = (d - ANCHOR).days
    hari = HARI[(4 + delta) % 7]      # Senin=0; delta 0 harus = Jumat (index 4)
    pasaran = PASARAN[(0 + delta) % 5]  # Legi=0; delta 0 harus = Legi
    return hari, pasaran


def neptu(hari, pasaran):
    return NEPTU_HARI[hari] + NEPTU_PASARAN[pasaran]


def watak(hari, pasaran):
    return WATAK_HARI[hari] + " " + WATAK_PASARAN[pasaran]


def jodoh_kategori(total_neptu):
    kat = JODOH_KATEGORI[total_neptu % 5]
    return kat, JODOH_KALIMAT[kat]


def _parse_tgl(s):
    return datetime.datetime.strptime(s, "%d-%m-%Y").date()


def _quota_ok(conn, user_id, admin_id):
    if is_vip(conn, user_id, admin_id):
        return True
    return get_quota_today(conn, "primbon", user_id) < PRIMBON_LIMIT


def _quota_habis_msg():
    return (
        f"Jatah primbon lu hari ini habis ({PRIMBON_LIMIT}/hari). "
        "Besok lagi ya, atau upgrade Premium biar unlimited."
    )


async def zodiak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    admin_id = context.bot_data.get("admin_id")

    if not context.args:
        await update.message.reply_text(
            "Pakai: /zodiak <tgl lahir>\nContoh: /zodiak 17-08-1945"
        )
        return
    try:
        d = _parse_tgl(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Format tanggalnya salah. Pakai dd-mm-yyyy ya.\n"
            "Contoh: /zodiak 17-08-1945"
        )
        return
    if not _quota_ok(conn, user.id, admin_id):
        await update.message.reply_text(_quota_habis_msg())
        return

    nama, rentang = zodiak_of(d.day, d.month)
    aspek, teks = ramalan(nama, datetime.date.today().timetuple().tm_yday)
    inc_quota_today(conn, "primbon", user.id)
    await update.message.reply_text(
        f"🔮 Zodiak lu: {nama} ({rentang})\n"
        f"📅 Ramalan hari ini — {aspek}:\n{teks}"
    )


async def weton_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    admin_id = context.bot_data.get("admin_id")

    if not context.args:
        await update.message.reply_text(
            "Pakai: /weton <tgl lahir>\nContoh: /weton 17-08-1945"
        )
        return
    try:
        d = _parse_tgl(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Format tanggalnya salah. Pakai dd-mm-yyyy ya.\n"
            "Contoh: /weton 17-08-1945"
        )
        return
    if not _quota_ok(conn, user.id, admin_id):
        await update.message.reply_text(_quota_habis_msg())
        return

    hari, pasaran = weton_of(d)
    n = neptu(hari, pasaran)
    inc_quota_today(conn, "primbon", user.id)
    await update.message.reply_text(
        f"🌙 Weton: {hari} {pasaran}\n"
        f"📅 {d.strftime('%d-%m-%Y')}\n"
        f"🔢 Neptu: {n} ({NEPTU_HARI[hari]}+{NEPTU_PASARAN[pasaran]})\n"
        f"🧭 Watak: {watak(hari, pasaran)}"
    )


async def jodoh_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = context.bot_data["db"]
    upsert_user(conn, user.id, user.username)
    admin_id = context.bot_data.get("admin_id")

    if len(context.args) < 2:
        await update.message.reply_text(
            "Pakai: /jodoh <tgl1> <tgl2>\nContoh: /jodoh 17-08-1945 20-05-1990"
        )
        return
    try:
        d1 = _parse_tgl(context.args[0])
        d2 = _parse_tgl(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "Format tanggalnya salah. Pakai dd-mm-yyyy ya.\n"
            "Contoh: /jodoh 17-08-1945 20-05-1990"
        )
        return
    if not _quota_ok(conn, user.id, admin_id):
        await update.message.reply_text(_quota_habis_msg())
        return

    h1, p1 = weton_of(d1)
    h2, p2 = weton_of(d2)
    n1, n2 = neptu(h1, p1), neptu(h2, p2)
    total = n1 + n2
    kat, kalimat = jodoh_kategori(total)
    inc_quota_today(conn, "primbon", user.id)
    await update.message.reply_text(
        f"💑 {d1.strftime('%d-%m-%Y')}: {h1} {p1} (neptu {n1})\n"
        f"💑 {d2.strftime('%d-%m-%Y')}: {h2} {p2} (neptu {n2})\n"
        f"❤️ Total neptu: {total} → {kat}\n{kalimat}\n\n"
        "Cuma hiburan tradisi ya, jangan dibawa serius 😄"
    )


def register(app: Application, db):
    app.add_handler(CommandHandler("zodiak", zodiak_cmd))
    app.add_handler(CommandHandler("weton", weton_cmd))
    app.add_handler(CommandHandler("jodoh", jodoh_cmd))
    log.info("primbon plugin registered")
