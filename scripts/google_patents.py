#!/usr/bin/env python3
"""Fallback-клиент Google Patents для случаев, когда API Роспатента не покрывает
зарубежный документ (см. references/search-international.md, раздел 2).

Разбор блокировки, зафиксированный вживую 08.09.2026:

  * GET patents.google.com/patent/<ID>/<lang> — страница конкретного патента —
    в тестовой сессии блокировалась почти сразу, вплоть до первого запроса.
    Тело ответа — HTML «Sorry... but your computer or network may be sending
    automated queries», статус то 200, то 503 (тело одинаковое в обоих
    случаях). Независимая библиотека patent-client-agents (github.com/
    parkerhancock/patent-client-agents) ходит туда штатно при темпе 4 с/запрос
    — расхождение похоже на репутацию конкретного IP тестовой сессии, а не
    свойство самого ресурса. ЭТОТ СКРИПТ ТУДА ПРИНЦИПИАЛЬНО НЕ ХОДИТ — связка
    ниже проще и не зависит от парсинга DOM/HTML.
  * GET patents.google.com/xhr/query?url=... — тот же JSON-эндпоинт, которым
    пользуется сама страница поиска, — терпимее: в прогоне выдержал ~8-12
    запросов подряд без пауз, прежде чем тоже свалился в 503 после
    продолжительной активности. Числа не константа — переопределять по факту.
  * GET patentimages.storage.googleapis.com/<путь>.pdf — отдельный хост
    (облачное хранилище), НЕ блокируется вместе с patents.google.com. Если
    путь к PDF уже получен из более раннего успешного /xhr/query, PDF можно
    тянуть и после того, как patents.google.com целиком лёг.

Рабочая связка: search → взять patent.pdf из JSON → claims качает PDF с
отдельного хоста и извлекает текст через pdftotext (внешний бинарник, не
stdlib — есть в poppler-utils; если его нет, скрипт прямо об этом сообщает,
не притворяется, что текст извлечён).

    python google_patents.py search "(surveying pole photodetector)"
    python google_patents.py search '"измерительная штанга"' --country RU
    python google_patents.py claims 26/37/74/5232ba04f9da8e/US10725123.pdf
    # ^ путь — ЗНАЧЕНИЕ ПОЛЯ patent.pdf из ответа search (хешированный путь в бакете),
    #   НЕ значение поля id (вида patent/US10725123B2/en) — это разные форматы, id сюда
    #   не передавать, готового URL из него не получится.

ПРЕДУПРЕЖДЕНИЕ. Блокировка — не только код ответа: тело «Sorry...» приходит
и с HTTP 200. Скрипт проверяет тело, а не только статус. Если /xhr/query начал
отдавать «Sorry» — блокировка перестала быть точечной, скрипт не ретраит
patents.google.com дальше 1 раза; переключайтесь на Espacenet или на
зарубежные массивы API Роспатента (search-rospatent.md, раздел 6).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEARCH_BASE = "https://patents.google.com/xhr/query"
PDF_BASE = "https://patentimages.storage.googleapis.com/"
# Темп и ретраи — по образцу независимо найденной и активно поддерживаемой
# библиотеки patent-client-agents (см. search-international.md, раздел 4.3):
# 4 с между запросами, cooldown 90 с после 503 (не мгновенный переход дальше),
# до 4 попыток с бэкоффом — это надёжнее, чем 1 запрос/сек + 1 повтор, которые
# использовались в первой версии скрипта.
RATE_SLEEP = 4.0
RETRY_SLEEP = 3.0
COOLDOWN_SECONDS = 90.0
MAX_ATTEMPTS = 4
SORRY_MARKER = "automated queries"

# Cooldown — ПО ХОСТУ, не глобально: patentimages.storage.googleapis.com — отдельный
# хост, который (см. докстринг выше) не блокируется вместе с patents.google.com. Один
# общий таймер заставил бы claims простаивать в cooldown'е от search, хотя PDF-хост
# в это время обычно жив. Модульное состояние: пока не понадобилось потокобезопасное.
_cooldown_until: dict[str, float] = {}


def _fetch(url: str, binary: bool = False):
    """GET с бэкоффом и cooldown-паузой после 503/антибот-блока. Возвращает (данные, ошибка)."""
    host = urllib.parse.urlparse(url).netloc
    now = time.time()
    until = _cooldown_until.get(host, 0.0)
    if now < until:
        time.sleep(until - now)

    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return (body if binary else body.decode("utf-8", "replace")), None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if SORRY_MARKER in body:
                _cooldown_until[host] = time.time() + COOLDOWN_SECONDS
                if attempt == MAX_ATTEMPTS:
                    return None, (f"HTTP {e.code}: заблокировано Google (антибот-страница); "
                                   f"следующая попытка не раньше чем через {COOLDOWN_SECONDS:.0f} с")
                time.sleep(COOLDOWN_SECONDS)
                continue
            return None, f"HTTP {e.code}: {body[:200]}"
        except Exception as ex:                                      # noqa: BLE001
            if attempt == MAX_ATTEMPTS:
                return None, f"{type(ex).__name__}: {ex}"
            time.sleep(RETRY_SLEEP)
    return None, "недостижимо"


def search(query: str, country: str | None, limit: int):
    inner = f"q={query}"
    if country:
        inner += f"&country={country}"
    # ВАЖНО: inner целиком должен стать ОДНИМ значением параметра url=. Раньше здесь было
    # safe="=&", то есть "=" и "&" внутри inner НЕ экранировались — из-за этого "&country=RU"
    # превращался в отдельный, самостоятельный параметр запроса верхнего уровня (Google его
    # там не ждёт и молча игнорирует), а не оставался вложенным в url=, как в проверенном
    # рабочем формате (url=q%3D(...)%26country%3DRU из search-international.md, раздел 2.2).
    # safe="" — экранировать "=" и "&" тоже, чтобы вложенность сохранялась.
    url = SEARCH_BASE + "?url=" + urllib.request.quote(inner, safe="") + "&exp=&tags="
    text, err = _fetch(url)
    time.sleep(RATE_SLEEP)
    if err:
        return None, err
    if SORRY_MARKER in text:
        return None, "заблокировано Google (антибот-страница, тело содержит 'Sorry')"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as ex:
        return None, f"не JSON: {ex}"


def digest(res: dict, limit: int) -> None:
    results = (res or {}).get("results") or {}
    total = results.get("total_num_results", 0)
    hits = ((results.get("cluster") or [{}])[0]).get("result") or []
    print(f"Всего найдено: {total}. Показано: {min(limit, len(hits))}.\n")
    for h in hits[:limit]:
        p = h.get("patent", {})
        pid = h.get("id", "?")
        pdf = p.get("pdf") or ""
        fam = (p.get("family_metadata") or {}).get("aggregated", {}).get("country_status") or []
        fam_str = ", ".join(f"{c.get('country_code')}:{(c.get('best_patent_stage') or {}).get('state')}" for c in fam)
        print(f"{pid}")
        print(f"  {p.get('title', '').strip()}")
        print(f"  заявитель: {p.get('assignee', '?')}  |  публикация: {p.get('publication_number', '?')}")
        print(f"  даты: приоритет {p.get('priority_date', '?')} / подача {p.get('filing_date', '?')} "
              f"/ публикация {p.get('publication_date', '?')}")
        if fam_str:
            print(f"  семья/статус: {fam_str}")
        print(f"  pdf: {pdf or '— недоступен через Google для этого хита'}")
        snippet = re.sub(r"\s+", " ", p.get("snippet", "")).strip()
        if snippet:
            print(f"  фрагмент (НЕ дословный п.1, усечён): {snippet[:200]}")
        print()


def fetch_pdf(pdf_path: str, out: str) -> tuple[str | None, str | None]:
    """pdf_path — относительный путь из поля patent.pdf либо готовый URL."""
    url = pdf_path if pdf_path.startswith("http") else PDF_BASE + pdf_path.lstrip("/")
    data, err = _fetch(url, binary=True)
    time.sleep(RATE_SLEEP)
    if err:
        return None, err
    with open(out, "wb") as f:
        f.write(data)
    return out, None


def extract_claims(pdf_file: str) -> tuple[str | None, str | None]:
    exe = shutil.which("pdftotext")
    if not exe:
        return None, ("pdftotext не найден в PATH (входит в poppler-utils). "
                       "Без него дословный текст из PDF не извлечь — не притворяемся, что извлекли.")
    try:
        out = subprocess.run([exe, "-layout", pdf_file, "-"],
                              capture_output=True, text=True, timeout=60, check=True)
    except Exception as ex:                                          # noqa: BLE001
        return None, f"pdftotext упал: {ex}"
    text = out.stdout
    if not text.strip():
        return None, "PDF без текстового слоя (скан) — текст формулы не извлечён"
    # Только точные маркеры начала раздела формулы, со словесной границей — иначе
    # ловится «disclaimer» и библиографическая строка «20 Claims, 10 Drawing Sheets».
    m = re.search(r"\b(What is claimed is:|The invention claimed is:|I claim:)\s*(.{0,4000})",
                   text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(0), None
    # Ни один из трёх маркеров не найден (не-US документ, другая формулировка) —
    # правдоподобная, но не подтверждённая догадка: раздел формулы обычно ближе
    # к концу документа. Помечаем явно, чтобы не выдать догадку за находку.
    return "⚠️ маркер раздела формулы не найден, показан хвост документа (не проверено, что это формула):\n\n" + text[-3000:], None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="поиск через /xhr/query (JSON, не HTML)")
    s.add_argument("query", help='пример: "(surveying pole photodetector)"')
    s.add_argument("--country", default=None, help="код страны, напр. RU")
    s.add_argument("--limit", type=int, default=10)

    c = sub.add_parser("claims", help="скачать PDF и извлечь текст формулы")
    c.add_argument("pdf_path", help="значение patent.pdf из search, либо готовый URL")
    c.add_argument("--keep", action="store_true", help="не удалять временный PDF")

    a = ap.parse_args()

    if a.cmd == "search":
        res, err = search(a.query, a.country, a.limit)
        if err:
            print("ОШИБКА:", err)
            print("Следующий шаг: patents.google.com недоступен целиком или частично; см. "
                  "references/search-international.md, раздел 2.3 — переключиться на Espacenet "
                  "или зарубежные массивы API Роспатента.")
            return 1
        digest(res, a.limit)
        return 0

    if a.cmd == "claims":
        import os
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            path = tmp.name
        try:
            saved, err = fetch_pdf(a.pdf_path, path)
            if err:
                print("ОШИБКА скачивания PDF:", err)
                return 1
            text, err2 = extract_claims(saved)
            if err2:
                print("ОШИБКА извлечения текста:", err2)
                return 1
            print(text)
            return 0
        finally:
            # Убрать временный PDF при любом исходе (успех, обе ошибки), кроме --keep —
            # иначе повторные неудачные попытки (частый случай: блокировка, скан без
            # текстового слоя) молча копят файлы во временном каталоге.
            if not a.keep and os.path.exists(path):
                os.unlink(path)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
