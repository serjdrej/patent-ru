#!/usr/bin/env python3
"""Ссылочная целостность формулы изобретения и безопасная перенумерация.

Отправная точка для скилла patent-ru. Проверяет то, что в живом деле
№ ⟦НОМЕР ЗАЯВКИ⟧ проверялось вручную и один раз чуть не было упущено.

Что ловит:
  * висящие ссылки «по п. N», где пункта N нет;
  * ссылку независимого пункта на зависимый — позиция экспертизы ФИПС:
    такая ссылка втягивает признаки и уничтожает самостоятельность притязания;
  * ссылку вперёд (пункт ссылается на последующий);
  * разрывы и повторы в нумерации;
  * пункты, на которые никто не ссылается (справочно, нарушением не является).

Режимы:
    python claims_integrity.py check   ФОРМУЛА.md
    python claims_integrity.py renum   ФОРМУЛА.md --drop 32-36
    python claims_integrity.py renum   ФОРМУЛА.md --keep 32-36 --rebase 1
"""
from __future__ import annotations

import argparse
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLAIM_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.*)$")
# «по п. 7», «по пп. 1-3», «по п. 32 или 33», «по любому из пп. 1-5»
# (?:любому\s+из\s+)? — тот самый вариант из документации, раньше не распознавался вовсе.
REF_RE = re.compile(r"по\s+(?:любому\s+из\s+)?п{1,2}\.?\s*([\d\s,\-–или]+?)(?=[,\s]*(?:отличающ|$|[,.]))", re.I)
NUM_RE = re.compile(r"\d{1,3}")
RANGE_RE = re.compile(r"(\d{1,3})\s*[-–]\s*(\d{1,3})")
# Зависимый пункт развивает ТОТ ЖЕ объект: за ссылкой рано или поздно идёт «отличающийся»,
# но между ними может стоять обычный текст ограничительной части («включающий корпус, …»),
# не только markdown-выделение. Независимый может ссылаться на другой пункт, обозначая
# назначение или целое, вообще не доходя до «отличающийся» после этой ссылки:
# «Гибридная система …, предназначенная для осуществления способа по п.1, содержащая…»
# «Опорный узел системы по п. 16, включающий корпус, … отличающийся тем, что…»
# Граница {0,80} — не опечатка, а откалиброванная эвристика: на живой формуле дела
# зависимые пункты дают разрыв ссылка→«отличающийся» в 2 символа (просто «, »), а
# независимые пункты, которые сами ссылаются на другой пункт для обозначения целого
# и затем через сотни символов перечисления состава доходят до СВОЕГО «отличающийся»
# (сочетание «способ + продукт для его осуществления» по п.63 Требований —
# см. draft-claims.md), дают разрыв от ~170 символов. 80 — с запасом посередине;
# держит и документированный пример («включающий корпус, …» ≈ 20 символов).
# Если на другой формуле окажется независимый пункт с более коротким перечислением
# состава между ссылкой и своим «отличающийся» — эвристика ошибётся; регексом такой
# случай не отличить надёжно от истинно зависимого пункта, это структурный предел метода.
DEPENDENT_RE = re.compile(r"по\s+(?:любому\s+из\s+)?п{1,2}\.?\s*[\d\s,\-–или]+.{0,80}?отличающ", re.I)


def parse(path: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in open(path, encoding="utf-8").read().splitlines():
        m = CLAIM_RE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def _expand_numbers(group_text: str) -> list[int]:
    """«1-5» → 1,2,3,4,5; «32 или 33» / «1, 3» → сами числа. Диапазон — по обеим границам."""
    nums: set[int] = set()
    for m in RANGE_RE.finditer(group_text):
        a, b = int(m.group(1)), int(m.group(2))
        if a <= b:
            nums.update(range(a, b + 1))
    remainder = RANGE_RE.sub(" ", group_text)   # границы диапазонов уже учтены — не задвоить
    nums.update(int(x) for x in NUM_RE.findall(remainder))
    return sorted(nums)


def refs_of(text: str) -> list[int]:
    nums: list[int] = []
    for m in REF_RE.finditer(text):
        nums += _expand_numbers(m.group(1))
    return sorted(set(nums))


def check(claims: list[tuple[int, str]]) -> int:
    nums = [n for n, _ in claims]
    ref_map = {n: refs_of(t) for n, t in claims}
    dependent = {n for n, t in claims if DEPENDENT_RE.search(t)}
    independent = set(nums) - dependent
    problems = 0

    print(f"Пунктов: {len(claims)}. Независимых: {len(independent)} "
          f"({', '.join(map(str, sorted(independent)))}).")
    linked = sorted(n for n in independent if ref_map.get(n))
    if linked:
        print(f"  · независимые со ссылкой на другой пункт: "
              f"{', '.join(f'{n}→{ref_map[n]}' for n in linked)}")

    dup = {n for n in nums if nums.count(n) > 1}
    if dup:
        print(f"  ✗ повторы номеров: {sorted(dup)}"); problems += 1
    gaps = [i for i in range(min(nums), max(nums)) if i not in nums] if nums else []
    if gaps:
        print(f"  ✗ разрывы нумерации: {gaps}"); problems += 1

    for n, targets in sorted(ref_map.items()):
        for t in targets:
            if t not in nums:
                print(f"  ✗ п.{n}: висящая ссылка на несуществующий п.{t}"); problems += 1
            elif t >= n:
                print(f"  ✗ п.{n}: ссылка вперёд, на п.{t}"); problems += 1
            elif t not in independent and n in independent:
                print(f"  ✗ п.{n} независимый, но ссылается на зависимый п.{t}"); problems += 1

    orphans = sorted(set(nums) - {t for r in ref_map.values() for t in r} - independent)
    if orphans:
        print(f"  · на пункты {orphans} никто не ссылается (не нарушение)")

    print("  ✓ ссылочная целостность в порядке" if not problems
          else f"  ИТОГО проблем: {problems}")
    return problems


def span(s: str) -> set[int]:
    out: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part or "–" in part:
            a, b = re.split(r"[-–]", part, maxsplit=1)
            out |= set(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def renumber(claims, drop: set[int], rebase: int):
    kept = [(n, t) for n, t in claims if n not in drop]
    mapping = {old: rebase + i for i, (old, _) in enumerate(kept)}
    out = []
    for old, text in kept:
        def sub(m):
            body = m.group(1)
            return "по п" + ("п" if len([1 for _ in NUM_RE.finditer(body)]) > 1 else "") + ". " + \
                   NUM_RE.sub(lambda x: str(mapping.get(int(x.group()), f"⟦{x.group()}?⟧")), body).strip()
        out.append((mapping[old], REF_RE.sub(sub, text)))
    return out, mapping


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("path")
    r = sub.add_parser("renum"); r.add_argument("path")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--drop", help="номера к исключению, напр. 32-36")
    g.add_argument("--keep", help="номера к сохранению, остальные исключаются")
    r.add_argument("--rebase", type=int, default=1)
    r.add_argument("--emit", action="store_true", help="печатать перенумерованный текст")
    a = ap.parse_args()

    claims = parse(a.path)
    if not claims:
        sys.exit("пункты формулы не найдены — ожидается строка вида «12. Текст…»")

    if a.cmd == "check":
        return 1 if check(claims) else 0

    allnums = {n for n, _ in claims}
    drop = span(a.drop) if a.drop else allnums - span(a.keep)
    out, mapping = renumber(claims, drop, a.rebase)
    print(f"Исключено {len(drop)}, осталось {len(out)}. Отображение номеров:")
    print("  " + ", ".join(f"{o}→{n}" for o, n in mapping.items() if o != n) or "  без сдвигов")
    print()
    if a.emit:
        for n, t in out:
            print(f"{n}. {t}\n")
    print("Проверка результата:")
    return 1 if check(out) else 0


if __name__ == "__main__":
    raise SystemExit(main())
