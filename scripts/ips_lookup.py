#!/usr/bin/env python3
"""Read consolidated per-redaction text of a federal act from ИПС «Законодательство России».

This is the official state system at ``http://pravo.gov.ru/proxy/ips/``. Unlike
``pravo_lookup.py``, which queries the *publication* portal and can only confirm
that an act exists and was amended, this module retrieves the **consolidated text
of a chosen redaction** — the thing the rest of this repository has always had to
mark as unavailable. Verified on 2026-09-16 against Федеральный закон от
03.08.2018 № 342-ФЗ (``nd=102479196``): редакция 6 returns ч. 23 ст. 26 in the
wording introduced by 496-ФЗ, редакции 0 and 5 return the superseded wording.

Three things about this system dictate the shape of this module.

*Encoding.* Every page is **windows-1251**, and so is the percent-encoding of
query parameters. Decoding a page as UTF-8 yields tens of thousands of characters
of plausible-looking garbage rather than an error, so the charset is hard-coded
here and never guessed.

*Transport.* Plain ``http://`` is used deliberately. On the hosts this repository
runs on, HTTPS to pravo.gov.ru hangs while HTTP answers immediately — the same
finding already recorded for ``publication.pravo.gov.ru``.

*Frames.* The search form is a legacy frameset: submitting it returns a frameset
whose ``<noframes>`` body is the only text a naive client sees, which is why a
direct query to ``?searchres=`` looks like it silently returns nothing. The real
result list lives at ``?list_itself=&...``, which this module requests directly.

Retrieval is per-act and deliberately small: ``find`` issues exactly one request
and returns at most one page of results (20 items, the site's own page size) with
the site's own total alongside; there is **no pagination, no crawling and no bulk
or batch mode**. ``redactions`` issues two requests (the document header and its
attribute card, the second only to cross-check the first). ``text`` issues one.

Exit codes follow ``pravo_lookup.py``: ``0`` success, ``1`` a genuine empty result
(no such act, no such article), ``2`` the tool or the site failed. An empty result
and a broken parser never share a channel — a result page that reports a non-zero
document count but parses to no items is an error, and an act whose card says
«Действует с изменениями» but whose redaction list parses to a single entry is an
error, not a short history.

Two limits of the source itself, both surfaced rather than smoothed over. The
redaction list carries the **signing date and number of each amending act, not
the date on which each redaction entered into force**, so this module cannot
state which redaction was in force on a given date; ``redactions --on-date``
returns only an explicitly-labelled upper bound. And the system does not hold
consolidated text for every redaction it lists: some carry «(не готова)» and
answer a text request with an empty RTF stub. For АПК РФ that is 25 of the 90
listed redactions (indices 30-54, 2014-2018). Those are reported in
``redactions_without_text``, and ``text`` returns them as an explicit empty
result rather than as a failure or as some other redaction's text.
"""

from __future__ import annotations

import argparse
import email
import html as html_module
import json
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "http://pravo.gov.ru/proxy/ips/"
CHARSET = "windows-1251"
TIMEOUT_SECONDS = 180
PAGE_SIZE = 20
BPAS_FEDERAL = "cd00000"
MIN_PLAUSIBLE_TEXT_CHARS = 200
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

MISSING_DOCUMENT_MARKER = "ДОКУМЕНТ ОТСУТСТВУЕТ"
ITEM_SEPARATOR = "<!-- BEGIN элемент списка -->"

ON_DATE_CAVEAT = (
    "Список редакций содержит дату ПОДПИСАНИЯ изменяющего акта, а не дату "
    "вступления его в силу. Поэтому редакцию, действовавшую на заданную дату, "
    "система здесь не сообщает. Приведённое значение - только ВЕРХНЯЯ ГРАНИЦА: "
    "действовавшая редакция имеет этот номер или меньший. Для точного ответа "
    "нужна дата вступления в силу изменяющего закона."
)

DISCOVERY_CAVEAT = (
    "ИПС «Законодательство России» - официальная государственная система, но "
    "полученный текст всё равно следует сверять с официальной публикацией "
    "(publication.pravo.gov.ru, см. pravo_lookup.py) перед использованием в "
    "процессуальном документе."
)


class IpsError(RuntimeError):
    """A network, HTTP or page-shape problem that must never be hidden."""


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


def _get(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            if not 200 <= response.status < 300:
                raise IpsError(f"Unexpected HTTP status {response.status} from {url}.")
            return response.read()
    except HTTPError as exc:
        raise IpsError(
            f"HTTP {exc.code} from {url} ({exc.reason}). This system answers 500 for a "
            "nonexistent nd and for an out-of-range rdk; check the identifier with "
            "`redactions` before assuming the site is broken."
        ) from exc
    except URLError as exc:
        raise IpsError(
            f"Network error reaching {url}: {exc.reason!s}. Note that this module uses "
            "plain HTTP on purpose - HTTPS to pravo.gov.ru hangs on the hosts this "
            "repository runs on. If HTTP also fails, do not assume a result."
        ) from exc
    except TimeoutError as exc:
        raise IpsError(f"Timed out after {TIMEOUT_SECONDS} seconds reaching {url}.") from exc


def _url(mode: str, params: list[tuple[str, str]]) -> str:
    query = urlencode([(mode, "")] + params, encoding=CHARSET)
    return f"{BASE_URL}?{query}"


def _decode(raw: bytes) -> str:
    return raw.decode(CHARSET, "replace")


def _superscript_classes(fragment: str) -> set[str]:
    """Which CSS classes this document uses to render a superscript."""
    classes: set[str] = set()
    for style in re.findall(r"(?is)<style.*?</style>", fragment):
        for selector, body in re.findall(r"([^{}]{0,120})\{([^{}]*)\}", style):
            if re.search(r"vertical-align\s*:\s*super", body, re.I):
                classes.update(re.findall(r"\.([A-Za-z][\w-]*)", selector))
    return classes


def _restore_article_subnumbers(fragment: str) -> str:
    """Turn a superscript article subnumber into the dotted form lawyers write.

    The export renders "статья 57.1" as `статья 57<span class="W9">1</span>`,
    with the superscript carried by a CSS class rather than a `<sup>` tag. Strip
    the tags naively and it becomes "статьи 571" — an article that does not
    exist, 241 times in one redaction of 342-ФЗ alone. Worse than ugly: a search
    of the retrieved text for "57.1" then returns nothing, and nothing reads as
    "this article is not mentioned" when in fact it is all over the document.
    The classes are read from the document's own stylesheet, not hard-coded,
    because the generator is free to rename them.
    """
    classes = _superscript_classes(fragment)
    if not classes:
        return fragment
    names = "|".join(sorted(re.escape(c) for c in classes))
    return re.sub(
        rf'(?is)(\d)\s*<span\s+class="(?:{names})"\s*>\s*(\d+)\s*</span>',
        r"\1.\2",
        fragment,
    )


def _strip_html(fragment: str) -> str:
    fragment = _restore_article_subnumbers(fragment)
    fragment = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(?:p|div|tr|li|td|h[1-6])\s*>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html_module.unescape(fragment).replace("\xa0", " ")
    fragment = re.sub(r"[ \t]+", " ", fragment)
    fragment = re.sub(r" *\n *", "\n", fragment)
    return re.sub(r"\n{3,}", "\n\n", fragment).strip()


def _one_line(fragment: str) -> str:
    return re.sub(r"\s+", " ", _strip_html(fragment)).strip()


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def _parse_list_item(block: str) -> dict[str, Any] | None:
    link = re.search(r'(?is)<div class="l_link">\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block)
    if link is None:
        return None
    nd = re.search(r"nd=(\d+)", link.group(1))
    if nd is None:
        return None
    header = _one_line(link.group(2))
    title = ""
    after = block[link.end() :]
    title_match = re.search(r'(?is)<span class="bold">(.*?)</span>', after)
    if title_match is not None:
        title = _one_line(title_match.group(1))
    status_match = re.search(r'(?is)<span class="tiny_italic_bold">(.*?)</span>', block)
    publications = [
        _one_line(li)
        for li in re.findall(r"(?is)<li[^>]*class='tiny'[^>]*>(.*?)</li>", block)
    ]
    eo_number = None
    for line in publications:
        if "pravo.gov.ru" in line:
            eo = re.search(r"ст\.\s*(\d{12,})", line)
            if eo is not None:
                eo_number = eo.group(1)
            break
    parsed_date = re.search(r"от\s+(\d{2}\.\d{2}\.\d{4})", header)
    parsed_number = re.search(r"№\s*(\S+)\s*$", header)
    return {
        "nd": nd.group(1),
        "header": header,
        "date": parsed_date.group(1) if parsed_date else None,
        "number": parsed_number.group(1) if parsed_number else None,
        "title": title,
        "status": _one_line(status_match.group(1)) if status_match else None,
        "eo_number": eo_number,
        "publications": publications,
        "document_url": f"{BASE_URL}?docbody=&nd={nd.group(1)}",
    }


def find(
    title: str | None = None,
    number: str | None = None,
    date: str | None = None,
    bpas: str = BPAS_FEDERAL,
    limit: int = PAGE_SIZE,
) -> dict[str, Any]:
    """One search request against the result-list frame. No pagination, ever."""
    if not any([title, number, date]):
        raise IpsError("find() needs at least one of title, number or date.")
    if date is not None and not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", date):
        raise IpsError(f"date must be DD.MM.YYYY, got {date!r}.")

    params: list[tuple[str, str]] = [("bpas", bpas)]
    if number:
        params += [("a8", number), ("a8type", "2")]
    if date:
        params += [("a7type", "1"), ("a7date", date)]
    if title:
        params.append(("a1", title))
    params += [("sort", "7"), ("page", "1")]

    url = _url("list_itself", params)
    raw = _get(url)
    query = {"title": title, "number": number, "date": date, "bpas": bpas}

    # The site answers a zero-result search with an empty body. That is its own
    # honest "nothing found", and it is kept strictly apart from a parse failure.
    if not raw.strip():
        return {
            "ok": True,
            "query": query,
            "url": url,
            "total_reported": 0,
            "returned": 0,
            "empty_result": True,
            "documents": [],
        }

    page = _decode(raw)
    size_match = re.search(r"listSize\s*=\s*(\d+)", page)
    if size_match is None:
        raise IpsError(
            f"{url} returned {len(raw)} bytes but no listSize marker. The result page "
            "shape has changed; this is a parser failure, not an empty result."
        )
    total = int(size_match.group(1))

    documents: list[dict[str, Any]] = []
    for block in page.split(ITEM_SEPARATOR)[1:]:
        item = _parse_list_item(block)
        if item is not None:
            documents.append(item)
        if len(documents) >= limit:
            break

    if total > 0 and not documents:
        raise IpsError(
            f"{url} reported {total} documents but no result item could be parsed. "
            "This is a parser failure and must not be reported as an empty result."
        )
    if total == 0:
        return {
            "ok": True,
            "query": query,
            "url": url,
            "total_reported": 0,
            "returned": 0,
            "empty_result": True,
            "documents": [],
        }

    return {
        "ok": True,
        "query": query,
        "url": url,
        "total_reported": total,
        "returned": len(documents),
        "page_size": PAGE_SIZE,
        "truncated": total > len(documents),
        "truncation_note": (
            "Показана только первая страница результатов; постраничного обхода нет "
            "по замыслу. Сузьте запрос номером и датой."
            if total > len(documents)
            else None
        ),
        "documents": documents,
        "caveat": DISCOVERY_CAVEAT,
    }


# ---------------------------------------------------------------------------
# redactions
# ---------------------------------------------------------------------------


def _fetch_card(nd: str) -> dict[str, Any]:
    url = f"{BASE_URL}?doc_itself=&vkart=card&nd={nd}&page=1"
    text = _strip_html(_decode(_get(url)))
    status = re.search(r"(?m)^(Действует[^\n]*|Утратил[^\n]*|Не вступил[^\n]*)$", text)
    return {"url": url, "text": text, "status": status.group(1).strip() if status else None}


def redactions(nd: str, on_date: str | None = None) -> dict[str, Any]:
    """The redaction list, cross-checked against the act's own status line."""
    if not re.fullmatch(r"\d+", nd):
        raise IpsError(f"nd must be digits, got {nd!r}.")
    url = f"{BASE_URL}?docbody=&nd={nd}"
    page = _decode(_get(url))

    if MISSING_DOCUMENT_MARKER in page:
        return {"ok": True, "nd": nd, "url": url, "not_found": True, "redactions": []}

    title_match = re.search(r"(?is)<title>(.*?)</title>", page)
    select = re.search(r'(?is)<select name="doc_editions".*?</select>', page)
    if select is None:
        raise IpsError(
            f"{url} returned {len(page)} characters but no doc_editions selector. The "
            "document page shape has changed; this is a parser failure."
        )

    entries: list[dict[str, Any]] = []
    pending: list[str] = []
    unreadable = 0
    for attrs, label_html in re.findall(r"(?is)<option([^>]*)>(.*?)</option>", select.group(0)):
        label = _one_line(label_html)
        if "disabled" in attrs.lower():
            # A future redaction whose text the system has not prepared yet.
            pending.append(label)
            continue
        value = re.search(r"value='(\d+),", attrs)
        # Historical redactions the system lists but has no text for carry
        # value='n' and are labelled "(не готова)". Dropping them silently would
        # turn a partial history into an apparently complete one.
        available = value is not None
        if not available and "value='n'" not in attrs:
            unreadable += 1
            continue
        ordinal = re.match(r"(\d+)\s*-", label)
        if value is not None:
            rdk: int | None = int(value.group(1))
        elif ordinal is not None:
            rdk = int(ordinal.group(1))
        elif not entries:
            rdk = 0
        else:
            rdk = None
        act_match = re.search(r"от\s+(\d{2}\.\d{2}\.\d{4})\s*№\s*([^\s(]+)", label)
        entries.append(
            {
                "rdk": rdk,
                "label": label,
                "amending_act_signed": act_match.group(1) if act_match else None,
                "amending_act_number": act_match.group(2) if act_match else None,
                "text_available": available,
                "current": "selected" in attrs.lower(),
            }
        )

    if not entries:
        raise IpsError(f"{url} has a doc_editions selector with no readable options.")
    if unreadable:
        raise IpsError(
            f"{url}: {unreadable} of the doc_editions options have an unrecognised "
            "value format. Returning the rest would be a silently partial history."
        )

    card = _fetch_card(nd)
    status = card["status"]
    # An act the system itself calls amended cannot honestly have one redaction.
    if status and "изменениями" in status and "без изменений" not in status and len(entries) < 2:
        raise IpsError(
            f"nd={nd} is reported by the system as {status!r} but only "
            f"{len(entries)} redaction(s) parsed. A stale or partial history must not "
            "be returned as a complete one."
        )

    current = next((entry["rdk"] for entry in entries if entry["current"]), None)
    without_text = [entry["label"] for entry in entries if not entry["text_available"]]
    result: dict[str, Any] = {
        "ok": True,
        "nd": nd,
        "url": url,
        "card_url": card["url"],
        "title": _one_line(title_match.group(1)) if title_match else None,
        "status": status,
        "count_listed": len(entries),
        "count_with_text": len(entries) - len(without_text),
        "current_rdk": current,
        "redactions": entries,
        "pending_redactions": pending,
        "redactions_without_text": without_text,
        "text_gap_note": (
            f"{len(without_text)} из {len(entries)} перечисленных редакций помечены "
            "«(не готова)»: система знает об изменении, но сводного текста этой "
            "редакции у неё нет. Запрос `text --rdk` по ним вернёт пустой результат."
            if without_text
            else None
        ),
        "dates_are": "signing dates of the amending acts, NOT dates of entry into force",
        "order_note": (
            "Порядок редакций задаёт сама система (он отражает вступление в силу), "
            "и он не всегда монотонен по дате подписания."
        ),
    }

    if on_date is not None:
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", on_date):
            raise IpsError(f"--on-date must be DD.MM.YYYY, got {on_date!r}.")
        day, month, year = (int(part) for part in on_date.split("."))
        key = (year, month, day)
        bound = 0
        for entry in entries:
            signed = entry["amending_act_signed"]
            if signed is None or entry["rdk"] is None:
                continue
            sday, smonth, syear = (int(part) for part in signed.split("."))
            if (syear, smonth, sday) <= key:
                bound = max(bound, entry["rdk"])
        bounded = next((entry for entry in entries if entry["rdk"] == bound), None)
        result["upper_bound_by_signing_date"] = {
            "date": on_date,
            "rdk_at_most": bound,
            "text_available_for_that_rdk": bool(bounded and bounded["text_available"]),
            "caveat": ON_DATE_CAVEAT,
        }
    return result


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


def _decode_mhtml(raw: bytes, url: str) -> str:
    message = email.message_from_bytes(raw)
    chunks: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or CHARSET
        try:
            chunks.append(payload.decode(charset, "replace"))
        except LookupError:
            chunks.append(payload.decode(CHARSET, "replace"))
    if not chunks:
        raise IpsError(f"{url} returned an archive with no decodable part.")
    return _strip_html("\n".join(chunks))


def document_text(nd: str, rdk: int | None = None) -> dict[str, Any]:
    """The consolidated text of one redaction, decoded from the MHTML export."""
    if not re.fullmatch(r"\d+", nd):
        raise IpsError(f"nd must be digits, got {nd!r}.")
    url = f"{BASE_URL}?savertf=&nd={nd}"
    if rdk is not None:
        if rdk < 0:
            raise IpsError("rdk must be zero or greater.")
        url += f"&rdk={rdk}"
    raw = _get(url)
    # A redaction the system lists but has never prepared answers with an empty
    # RTF stub instead of the usual MHTML archive. That is a real answer -- "no
    # text for this redaction" -- and must not be dressed up as either a tool
    # failure or, worse, as the text of some other redaction.
    if raw.lstrip()[:5] == b"{\\rtf":
        return {
            "ok": True,
            "nd": nd,
            "rdk": rdk,
            "url": url,
            "text_not_prepared": True,
            "bytes": len(raw),
            "note": (
                "Система вернула пустой RTF вместо сводного текста: эта редакция "
                "числится в перечне, но её текст не подготовлен («не готова»). "
                "Проверьте поле redactions_without_text в выводе `redactions`."
            ),
        }
    text = _decode_mhtml(raw, url)
    if len(text) < MIN_PLAUSIBLE_TEXT_CHARS:
        raise IpsError(
            f"{url} decoded to only {len(text)} characters. Either the export format "
            "changed or the wrong part was read; this is a failure, not a short act."
        )
    # The consolidated export names the amending acts in its own header. Returning
    # it lets a caller confirm that --rdk actually selected what it asked for.
    note = re.search(r"(?m)^\(В редакции[^\n]*\)$", text[:20000])
    return {
        "ok": True,
        "nd": nd,
        "rdk": rdk,
        "url": url,
        "chars": len(text),
        "amendment_note_in_text": note.group(0) if note else None,
        "text": text,
    }


def extract_article(text: str, number: str) -> str | None:
    escaped = re.escape(number)
    start = re.search(rf"(?m)^\s*Статья\s+{escaped}\s*$", text)
    if start is None:
        start = re.search(rf"(?m)^\s*Статья\s+{escaped}(?=[.\s])", text)
    if start is None:
        return None
    rest = text[start.end() :]
    # The end boundary must match every shape the heading takes, not only the
    # one where the article number sits alone on its line. ГК РФ puts the name
    # on the same line ("Статья 1364. Условия..."), so a pattern anchored with
    # `$` never fires there and the "article" silently runs to the end of the
    # document - 189 000 characters of articles 1363 through 1400, reported as
    # a successful extraction. Match a heading at the start of a line whatever
    # follows it.
    end = re.search(r"(?m)^\s*Статья\s+\d\S*", rest)
    body = rest[: end.start()] if end else rest
    article = (start.group(0).strip() + "\n" + body.strip()).strip()
    # The export puts the article heading and its name on separate lines, which
    # otherwise renders as "Статья 125\n. Форма и содержание искового заявления".
    return re.sub(r"(?m)\A(Статья\s+\S+?)\s*\n\s*\.\s*", r"\1. ", article)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Клиент ИПС «Законодательство России» (pravo.gov.ru/proxy/ips): "
            "поиск акта, список редакций, консолидированный текст редакции."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    finder = commands.add_parser("find", help="найти акт и его nd")
    finder.add_argument("title", nargs="?", default=None, help='например "Градостроительный кодекс"')
    finder.add_argument("--number", default=None, help='точный номер, например "342-ФЗ"')
    finder.add_argument("--date", default=None, help="дата подписания ДД.ММ.ГГГГ")
    finder.add_argument("--bpas", default=BPAS_FEDERAL, help="база (по умолчанию федеральная)")
    finder.add_argument("--limit", type=int, default=PAGE_SIZE)

    reds = commands.add_parser("redactions", help="список редакций акта")
    reds.add_argument("nd")
    reds.add_argument("--on-date", default=None, help="ДД.ММ.ГГГГ - только верхняя граница")

    text = commands.add_parser("text", help="текст редакции")
    text.add_argument("nd")
    text.add_argument("--rdk", type=int, default=None, help="номер редакции; без него - текущая")
    text.add_argument("--article", default=None, help="выделить одну статью, например 26")
    text.add_argument("--head", type=int, default=None, help="обрезать текст до N символов")
    return parser


def _run_find(args: argparse.Namespace) -> int:
    result = find(args.title, args.number, args.date, args.bpas, max(1, args.limit))
    _emit(result)
    return 1 if result.get("empty_result") else 0


def _run_redactions(args: argparse.Namespace) -> int:
    result = redactions(args.nd, args.on_date)
    _emit(result)
    return 1 if result.get("not_found") else 0


def _run_text(args: argparse.Namespace) -> int:
    result = document_text(args.nd, args.rdk)
    if result.get("text_not_prepared"):
        _emit(result)
        return 1
    if args.article is not None:
        body = extract_article(result["text"], args.article)
        result["article"] = args.article
        result["article_found"] = body is not None
        if body is None:
            result.pop("text", None)
            result["note"] = (
                f"Статья {args.article} не найдена в тексте этой редакции. Это "
                "содержательный отрицательный ответ (статья могла быть не в этом "
                "акте или утратить силу), а не сбой: текст получен и разобран "
                f"({result['chars']} символов)."
            )
            _emit(result)
            return 1
        result["text"] = body
        result["chars"] = len(body)
    if args.head is not None and len(result["text"]) > args.head:
        result["text"] = result["text"][: args.head]
        result["head_truncated"] = True
    _emit(result)
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover - older interpreters only
        pass
    args = make_parser().parse_args()
    try:
        if args.command == "find":
            return _run_find(args)
        if args.command == "redactions":
            return _run_redactions(args)
        return _run_text(args)
    except IpsError as exc:
        print(f"Lookup failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
