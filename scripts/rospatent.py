#!/usr/bin/env python3
"""Клиент API ИС «Поисковая платформа» Роспатента.

Отправная точка для скилла patent-ru. Собран из трёх боевых итераций прогона
08.09.2026 по заявке № ⟦НОМЕР ЗАЯВКИ⟧; все обходные пути ниже проверены на практике,
в официальной документации их нет.

Ключ берётся ТОЛЬКО из ROSPATENT_API_KEY. В код, в логи и в сохраняемые файлы
он не попадает.

    export ROSPATENT_API_KEY=<ключ>
    python rospatent.py search '(веха OR "измерительная штанга") AND инерциальн*'
    python rospatent.py doc RU2816552C1_20240401
    python rospatent.py doc RU2794881C1                 # дату подберёт сам
    python rospatent.py similar --file claim1.txt       # см. предупреждение ниже
    python rospatent.py datasets

ПРЕДУПРЕЖДЕНИЕ О similar_search. В боевом прогоне метод оказался непригоден как
доказательство: на текст п.1 формулы геодезического прибора (1448 знаков) выдал
100 документов с similarity_norm в диапазоне 0.9931-0.9942 — метрика не
различает. В первой двадцатке были МРТ-трекинг, конфигурация RRC (Ericsson),
классификация контента (Nokia). Использовать только как генератор идей для
последующих ключевых запросов; НИКОГДА — как подтверждение отсутствия аналогов.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # иначе '⚠' валит скрипт на Windows-консоли (cp1251)

BASE = "https://searchplatform.rospatent.gov.ru/patsearch/v0.2"
RU_FUND = ["RU", "SU"]          # советские АС входят в уровень техники
SAFE_LIMIT = 10                 # limit=20 на широких запросах падает с IncompleteRead
RATE_SLEEP = 1.0                # 1 запрос в секунду
RETRY_SLEEP = 3.0


# --------------------------------------------------------------------------- io
def _key() -> str:
    k = os.environ.get("ROSPATENT_API_KEY", "").strip()
    if not k:
        sys.exit("ROSPATENT_API_KEY не задана. Запуск отменён — ключ в код не вписывать.")
    return k


class Client:
    """Три-счёт (отправлено/получено/процитировано) ведётся автоматически."""

    def __init__(self, priority: str | None = None, outdir: str | None = None):
        self.key = _key()
        self.priority = (priority or "").replace("-", "").replace(".", "")[:8]
        self.outdir = outdir
        self.sent = 0
        self.received = 0
        self.failures: list[tuple[str, str]] = []
        if outdir:
            os.makedirs(outdir, exist_ok=True)

    def call(self, path: str, payload=None, method: str = "POST"):
        """Возвращает (данные, ошибка). Одна повторная попытка через 3 с."""
        url = f"{BASE}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        self.sent += 1
        for attempt in (1, 2):
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Authorization", f"Bearer {self.key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode("utf-8")), None
            except urllib.error.HTTPError as e:
                # 4xx повторять бессмысленно
                err = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:250]}"
                self.failures.append((path, err))
                return None, err
            except Exception as e:                                    # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                if attempt == 2:
                    self.failures.append((path, err))
                    return None, err
                time.sleep(RETRY_SLEEP)
        return None, "недостижимо"

    def save(self, name: str, obj) -> None:
        if not self.outdir:
            return
        with open(os.path.join(self.outdir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    # ------------------------------------------------------------------ методы
    def search(self, q: str, limit: int = SAFE_LIMIT, countries=None, **extra):
        if limit > SAFE_LIMIT:
            print(f"  [!] limit={limit} > {SAFE_LIMIT}: возможен IncompleteRead, "
                  f"добирайте через offset", file=sys.stderr)
        payload = {"q": q, "limit": limit, "sort": "relevance"}
        if countries is not False:
            payload["filter"] = {"country": {"values": countries or RU_FUND}}
        payload.update(extra)
        res, err = self.call("search", payload)
        if res:
            self.received += len(res.get("hits") or [])
        time.sleep(RATE_SLEEP)
        return res, err

    def doc(self, pid: str):
        """Полный документ.

        Идентификатор обязан иметь вид {CC}{номер}{вид}_{ГГГГММДД}. Если даты нет —
        достаём её через PN=, затем повторяем. Этого в документации нет.
        """
        res, err = self.call(f"docs/{pid}", None, "GET")
        time.sleep(RATE_SLEEP)
        if res:
            self.received += 1
            return res, None
        if "_" not in pid:                       # добираем дату публикации
            found, ferr = self.search(f"PN={pid}", limit=1, countries=False)
            hit = (found or {}).get("hits") or []
            if hit and hit[0].get("id"):
                res2, err2 = self.call(f"docs/{hit[0]['id']}", None, "GET")
                time.sleep(RATE_SLEEP)
                if res2:
                    self.received += 1
                    return res2, None
                return None, err2
            return None, ferr or err
        return None, err

    def similar(self, text: str, count: int = 50):
        print("  [!] similar_search: метрика не дискриминирует, см. docstring модуля",
              file=sys.stderr)
        res, err = self.call("similar_search",
                             {"type_search": "text_search",
                              "pat_text": text[:4000], "count": count})
        time.sleep(RATE_SLEEP)
        return res, err

    def datasets(self):
        res, err = self.call("datasets/tree", None, "GET")
        time.sleep(RATE_SLEEP)
        return res, err

    def audit(self) -> str:
        return (f"Запросов отправлено: {self.sent}. Документов получено: {self.received}. "
                f"Отказов источника: {len(self.failures)}.")


# ---------------------------------------------------------------- разбор ответа
def strip(s) -> str:
    """Снять HTML-теги и маркеры вида [135], схлопнуть пробелы."""
    return re.sub(r"\s+", " ", re.sub(r"\[\d+\]", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()


def deep(o, *keys):
    for k in keys:
        o = o.get(k) if isinstance(o, dict) else None
        if o is None:
            return None
    return o


def fields(doc: dict) -> dict:
    """Поля, которые реально нужны, из ответа /docs."""
    ru = deep(doc, "biblio", "ru") or deep(doc, "biblio", "en") or {}
    holders = ru.get("patentee") or []
    ipc = deep(doc, "common", "classification", "ipcr") or []
    return {
        "id": doc.get("id", ""),
        "title": strip(ru.get("title")),
        "patentee": ", ".join(strip(p.get("name", "")) for p in holders if isinstance(p, dict)),
        "ipc": ", ".join(strip(c.get("fullname", "")) for c in ipc if isinstance(c, dict)),
        "publication_date": deep(doc, "common", "publication_date") or "",
        "abstract": strip(deep(doc, "abstract", "ru")),
        "claims": strip(deep(doc, "claims", "ru")),
    }


def digest(res: dict, label: str, limit: int = 15, priority: str = "") -> None:
    """Компактная выжимка выдачи. Документы позже приоритета помечаются."""
    hits = (res or {}).get("hits") or []
    print(f"\n### {label} — всего {(res or {}).get('total', len(hits))}, "
          f"показано {min(limit, len(hits))}")
    for h in hits[:limit]:
        ru = deep(h, "biblio", "ru") or {}
        pid = h.get("id", "?")
        pd = deep(h, "common", "publication_date") or h.get("publication_date", "")
        flag = ""
        if priority and str(pd).replace("-", "").replace(".", "")[:8] > priority:
            flag = "  ⚠ ПОСЛЕ ПРИОРИТЕТА — в уровень техники не входит"
        sim = h.get("similarity_norm")
        tail = f" | sim={sim:.4f}" if isinstance(sim, (int, float)) else ""
        print(f"  {pid:26} {strip(ru.get('title'))[:88]}{tail}{flag}")


# ------------------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority", default="", help="дата приоритета ГГГГММДД или ГГГГ-ММ-ДД")
    ap.add_argument("--out", default="", help="каталог для сырых JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=SAFE_LIMIT)
    s.add_argument("--world", action="store_true", help="снять фильтр по RU/SU")

    d = sub.add_parser("doc")
    d.add_argument("pid", nargs="+")
    d.add_argument("--claims-chars", type=int, default=2000)

    m = sub.add_parser("similar")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--file")
    m.add_argument("--count", type=int, default=50)

    sub.add_parser("datasets")
    a = ap.parse_args()

    c = Client(priority=a.priority, outdir=a.out or None)

    if a.cmd == "search":
        res, err = c.search(a.query, limit=a.limit, countries=False if a.world else None)
        if err:
            print("ОШИБКА:", err); return 1
        c.save("search.json", res)
        digest(res, a.query[:70], a.limit, c.priority)

    elif a.cmd == "doc":
        for pid in a.pid:
            res, err = c.doc(pid)
            if err:
                print(f"\n### {pid}\n  ОШИБКА: {err}"); continue
            c.save(f"{res.get('id', pid)}.json", res)
            f = fields(res)
            print(f"\n{'=' * 96}\n### {f['id'] or pid}")
            for k in ("title", "patentee", "ipc", "publication_date"):
                if f[k]:
                    print(f"  {k:17}: {f[k][:130]}")
            if f["abstract"]:
                print(f"  {'abstract':17}: {f['abstract'][:600]}")
            if f["claims"]:
                print(f"  {'claims':17}: {f['claims'][:a.claims_chars]}")

    elif a.cmd == "similar":
        text = a.text or open(a.file, encoding="utf-8").read()
        res, err = c.similar(text, a.count)
        if err:
            print("ОШИБКА:", err); return 1
        c.save("similar.json", res)
        digest(res, "similar_search (метрика ненадёжна)", 25, c.priority)
        sims = [h.get("similarity_norm") for h in (res.get("hits") or [])
                if isinstance(h.get("similarity_norm"), (int, float))]
        if sims and max(sims) - min(sims) < 0.05:
            print(f"\n  [!] Разброс similarity_norm {min(sims):.4f}..{max(sims):.4f} — "
                  f"метрика не дискриминирует. Как доказательство не использовать.")

    elif a.cmd == "datasets":
        res, err = c.datasets()
        if err:
            print("ОШИБКА:", err); return 1
        print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])

    print("\n" + c.audit())
    for path, err in c.failures:
        print(f"  отказ: {path} -> {err[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
