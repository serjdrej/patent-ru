#!/usr/bin/env python3
"""Контроль новой материи и неопределённых обозначений (п.2 ст.1378 ГК РФ).

Отправная точка для скилла patent-ru. Воспроизводит две проверки, которые на
реальном деле автора (номер не приводится) дали результат, не найденный чтением.

Режим ngram — новая материя.
    Любой признак, отсутствовавший в первоначальных документах, меняет заявку
    по существу. Сверяем правленый текст с первоисточниками по совпадениям из
    N слов подряд (по умолчанию 5) и показываем участки, которых в источниках
    нет. На живом деле из 8 493 слов нашлось 64 таких участка, из них 54 —
    безвредные швы между переставленными предложениями. Короткие участки
    отсекайте --min-run.

Режим symbols — неопределённые обозначения.
    При исключении блоков описания вместе с ними уходит абзац с расшифровкой
    символов, и обозначение остаётся употреблённым без определения. Так в
    выделенной заявке осиротели Lbase_offset, ΔZphoto и Zant.

    python novelty_check.py ngram   НОВОЕ.md --source ИСХОДНОЕ.md --source ФОРМУЛА.md
    python novelty_check.py symbols НОВОЕ.md [--source ИСХОДНОЕ.md]
"""
from __future__ import annotations

import argparse
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WORD_RE = re.compile(r"[\wЀ-ӿ]+", re.U)

# Латиница, живущая в русском тексте как обозначение, а не как термин.
SYMBOL_RE = re.compile(
    r"(?<![\w])("
    r"[A-ZΔ][A-Za-z0-9]*\\?_[A-Za-z0-9]+"      # Lbase\_offset, Lant_offset
    r"|Δ[A-Za-z][A-Za-z0-9]*"                   # ΔZphoto, ΔZant
    r"|[A-Z][a-z]{1,6}[0-9]*"                   # Zant, Zmax, Z0
    r")(?![\w])"
)

# Аббревиатуры и обычные латинские вкрапления — не обозначения.
STOP = {
    "UWB", "GNSS", "GPS", "IMU", "CAN", "PWM", "LoRa", "BIM", "RTK", "LTE", "WiFi",
    "ГЛОНАСС", "Topcon", "Trimble", "Leica", "Fig", "US", "RU", "EP", "CN", "WO",
    "GDOP", "PDOP", "HDOP", "VDOP", "ID", "IP", "PC", "TOF", "AoA", "ToF",
}


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def shingles(ws: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def cmd_ngram(a) -> int:
    target_raw = open(a.target, encoding="utf-8").read()
    tw = words(target_raw)
    if len(tw) < a.n:
        sys.exit("целевой текст короче окна")

    known: set[tuple[str, ...]] = set()
    for s in a.source:
        known |= shingles(words(open(s, encoding="utf-8").read()), a.n)
    if not known:
        sys.exit("не задан ни один источник (--source)")

    # позиции слов, не покрытых ни одним известным N-словным совпадением
    covered = [False] * len(tw)
    for i in range(len(tw) - a.n + 1):
        if tuple(tw[i:i + a.n]) in known:
            for j in range(i, i + a.n):
                covered[j] = True

    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(tw):
        if not covered[i]:
            j = i
            while j < len(tw) and not covered[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1

    long_runs = [r for r in runs if r[1] - r[0] >= a.min_run]
    print(f"Слов в целевом тексте: {len(tw)}. Источников: {len(a.source)}. Окно: {a.n}.")
    print(f"Участков вне первоисточников: {len(runs)}; "
          f"из них длиной ≥{a.min_run} слов: {len(long_runs)}.")
    print("Короткие участки — как правило швы между переставленными предложениями.\n")
    for s, e in long_runs:
        ctx = " ".join(tw[max(0, s - 4):s])
        frag = " ".join(tw[s:e])
        print(f"  [{e - s:3} сл.] …{ctx} ⟪{frag}⟫")
    return 1 if long_runs else 0


def defined(sym: str, text: str) -> bool:
    """Определение — обозначение, за которым идёт тире и пояснение."""
    pat = re.escape(sym) + r"\s*[\\]?\s*[—–-]\s*[а-яёA-Za-z]"
    return re.search(pat, text) is not None


def cmd_symbols(a) -> int:
    text = open(a.target, encoding="utf-8").read()
    src = "".join(open(s, encoding="utf-8").read() for s in a.source) if a.source else ""

    # Токен, у которого сосед слева или справа — тоже латиница, принадлежит
    # английской фразе (заголовок из списка литературы, название фирмы), а не
    # обозначение. Символы с подчёркиванием, Δ или цифрой оставляем всегда.
    LAT = re.compile(r"[A-Za-z]")
    always = re.compile(r"[_Δ0-9]")

    found: dict[str, int] = {}
    for m in SYMBOL_RE.finditer(text):
        s = m.group(1)
        if s in STOP or s.upper() in STOP:
            continue
        if not always.search(s):
            left = text[max(0, m.start() - 24):m.start()]
            right = text[m.end():m.end() + 24]
            # Сосед, сам похожий на обозначение (Z0, ΔL), английской фразы не образует
            def prose(tok: str) -> bool:
                return bool(LAT.match(tok)) and not always.search(tok) and len(tok) > 2

            lw = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9Δ_\\]+", left) if t.strip("\\_")]
            rw = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9Δ_\\]+", right) if t.strip("\\_")]
            if (lw and prose(lw[-1])) or (rw and prose(rw[0])):
                continue                      # часть английской фразы
        found[s] = found.get(s, 0) + 1

    if not found:
        print("обозначений не найдено"); return 0

    orphans = []
    print(f"Обозначений в тексте: {len(found)}\n")
    for sym, cnt in sorted(found.items(), key=lambda x: -x[1]):
        ok = defined(sym, text)
        mark = "✓ определено" if ok else "✗ БЕЗ ОПРЕДЕЛЕНИЯ"
        note = ""
        if not ok and src and defined(sym, src):
            note = "  ← определение есть в первоисточнике, вернуть его (новой материей не будет)"
            orphans.append(sym)
        elif not ok:
            orphans.append(sym)
        print(f"  {sym:22} ×{cnt:<3} {mark}{note}")

    if orphans:
        print(f"\nБез определения: {', '.join(orphans)}")
        print("Позиционные номера и обозначения признаками не являются — "
              "возврат определения из первоначального описания новой материи не создаёт.")
    return 1 if orphans else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("ngram")
    g.add_argument("target")
    g.add_argument("--source", action="append", required=True)
    g.add_argument("-n", type=int, default=5)
    g.add_argument("--min-run", type=int, default=15)

    s = sub.add_parser("symbols")
    s.add_argument("target")
    s.add_argument("--source", action="append", default=[])

    a = ap.parse_args()
    return cmd_ngram(a) if a.cmd == "ngram" else cmd_symbols(a)


if __name__ == "__main__":
    raise SystemExit(main())
