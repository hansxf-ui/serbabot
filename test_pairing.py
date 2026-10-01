"""Unit test logika SerbaBot TANPA network / tanpa Telegram.

Jalankan dari folder serbabot:
    python3 test_pairing.py

Modul pairing & filtering di-load langsung dari file (bukan via package),
jadi TIDAK butuh python-telegram-bot terinstall.
"""
import importlib.util
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pairing_mod = load("pairing", "plugins/anonchat/pairing.py")
filtering_mod = load("filtering", "plugins/anonchat/filtering.py")
PairingManager = pairing_mod.PairingManager

PASS = []


def check(nama, cond):
    assert cond, f"GAGAL: {nama}"
    PASS.append(nama)
    print(f"  PASS {nama}")


print("== pairing ==")
p = PairingManager(timeout=600)

s, partner = p.search(1)
check("user 1 masuk antrean", s == "queued" and partner is None)

s, partner = p.search(2)
check("user 2 pair dengan 1", s == "paired" and partner == 1)
check("pasangan 1 adalah 2", p.get_partner(1) == 2)
check("pasangan 2 adalah 1", p.get_partner(2) == 1)

s, partner = p.search(1)
check("search saat sudah pair -> already", s == "already" and partner == 2)

s, partner = p.search(3)
check("user 3 masuk antrean", s == "queued")

s, partner = p.search(4)
check("user 4 pair dengan 3", s == "paired" and partner == 3)

s, partner = p.search(5)
check("user 5 antre (tidak pair dengan diri sendiri)", s == "queued")
s, partner = p.search(5)
check("search dua kali -> tetap queued", s == "queued")

st, partner = p.stop(5)
check("stop dari antrean -> dequeued", st == "dequeued" and partner is None)
check("user 5 sudah tidak di antrean", p.get_partner(5) is None)

st, partner = p.stop(1)
check("stop dari sesi -> unpaired + partner 2", st == "unpaired" and partner == 2)
check("sesi 1 bersih", p.get_partner(1) is None)
check("sesi 2 bersih", p.get_partner(2) is None)

st, partner = p.stop(99)
check("stop saat idle -> idle", st == "idle" and partner is None)

# timeout: bikin sesi lalu tuakan timestamp-nya
p2 = PairingManager(timeout=600)
p2.search(10)
p2.search(11)
assert p2.get_partner(10) == 11
p2._last_active[10] = 0  # pura-pura terakhir aktif lama banget
p2._last_active[11] = 0
dead = p2.expired(now=10_000)
check("sesi kadaluarsa terdeteksi", set(dead) == {10, 11})
st, partner = p2.stop(10)
check("stop sesi kadaluarsa memutus keduanya", st == "unpaired" and partner == 11)

# touch me-refresh timestamp
p3 = PairingManager(timeout=600)
p3.search(20)
p3.search(21)
p3._last_active[20] = 1000
p3._last_active[21] = 1000
p3.touch(20)
check("touch me-refresh user yg aktif", 20 not in p3.expired(now=1000 + 601))
check("partner yg diam tetap expired", 21 in p3.expired(now=1000 + 601))

print("== filtering ==")
badwords = filtering_mod.load_badwords()
check("badwords >= 40 kata", len(badwords) >= 40)
f = filtering_mod.contains_badword
check("kata kasar kedeteksi", f("kamu anjing banget", badwords))
check("case-insensitive", f("DASAR TOLOL", badwords))
check("kalimat bersih lolos", not f("halo, apa kabar?", badwords))
check("bukan substring ('janjian' aman)", not f("kita janjian jam 3", badwords))
check("'masuk' tidak kena filter 'asu'", not f("masuk kelas dulu", badwords))

print(f"\nSEMUA {len(PASS)} TEST HIJAU")
