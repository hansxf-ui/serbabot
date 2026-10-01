"""Filter kata kasar — stdlib doang (re + pathlib). Naive: cek per kata,
bukan substring. Daftar kata di badwords.txt, satu per baris."""

import re
from pathlib import Path

_TOKEN_RE = re.compile(r"[a-zA-Z]+")


def load_badwords(path=None):
    p = Path(path) if path else Path(__file__).with_name("badwords.txt")
    words = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        w = line.strip().lower()
        if w and not w.startswith("#"):
            words.add(w)
    return words


def contains_badword(text, badwords):
    tokens = set(_TOKEN_RE.findall(text.lower()))
    return not tokens.isdisjoint(badwords)
