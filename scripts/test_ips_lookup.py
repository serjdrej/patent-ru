#!/usr/bin/env python3
"""Live regression test for ``scripts/ips_lookup.py``, stdlib only.

This test makes REAL network requests to ``http://pravo.gov.ru/proxy/ips/``. It
is deliberately NOT a hermetic unit test: there is no mocked transport for the
main path, it depends on the live official system being reachable and behaving
as documented, and it can fail for reasons unrelated to any local change. It
makes roughly ten requests in total - not a load test, and it never paginates.

Its assertions are **comparative**, which is the lesson this repository paid for
twice: a test that only checks "HTTP 200 and some Cyrillic came back" would have
passed every bug shipped here. The decisive checks are

* the same act at two different ``--rdk`` values must return **different** text,
  and specifically must differ in the one clause that cost this project a wrong
  answer - ч. 23 ст. 26 Федерального закона от 03.08.2018 № 342-ФЗ, reworded by
  496-ФЗ of 28.12.2025;
* a query for an act that does not exist must produce an outcome different **in
  kind** from a real one (an explicit empty result, not a short list and not an
  exception), and a parser that returns nothing from a populated page must still
  fail loudly.

Run it directly:

    python scripts/test_ips_lookup.py
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import unittest

import ips_lookup
from ips_lookup import IpsError, document_text, extract_article, find, redactions


# Федеральный закон от 03.08.2018 № 342-ФЗ - the act whose staleness this
# repository failed to notice for nine months.
ND_342 = "102479196"
# Гражданский кодекс Российской Федерации, часть первая - amended >150 times,
# so a redaction list of length one for it is unambiguously a parser failure.
ND_GK1 = "102033239"
# Арбитражный процессуальный кодекс - the act that exposed the system's own gap:
# 25 of its listed redactions carry «(не готова)» and have no text at all.
ND_APK = "102079219"
RDK_APK_NOT_PREPARED = 30

# Постановление Правительства РФ от 24.10.2022 № 1885 - an act with no
# articles at all: it is divided into пункты, so `--article` on it can only
# ever fail, and the failure must not read as "такой нормы нет".
ND_POSTANOVLENIE = "603486228"

NONSENSE_TITLE = "зыфвуацйщкнесуществующийзаконъё918273465"

# Wording of ч. 23 ст. 26 introduced by 496-ФЗ (current redaction only).
WORDING_NEW = (
    "до дня вступления в силу положения, утвержденного в соответствии с "
    "пунктом 1 статьи 106 Земельного кодекса Российской Федерации"
)
# Wording it replaced (every redaction up to and including 5).
WORDING_OLD = "до дня официального опубликования настоящего Федерального закона"


def part_23(article_text: str) -> str:
    match = re.search(r"(?ms)^23\.\s.*?(?=^24\.\s|\Z)", article_text)
    return match.group(0) if match else ""


def run_text(*argv: str) -> tuple[int, dict]:
    """The `text` command as the CLI runs it, without a subprocess."""
    args = ips_lookup.make_parser().parse_args(["text", *argv])
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = ips_lookup._run_text(args)
    return code, json.loads(buffer.getvalue())


class AddressingTest(unittest.TestCase):
    """Going from a law's number and date to its nd, and the empty-result path."""

    found: dict
    empty: dict

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.found = find(number="342-ФЗ", date="03.08.2018")
            cls.empty = find(title=NONSENSE_TITLE)
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable or errored: {exc}") from exc

    def test_number_and_date_resolve_to_the_expected_nd(self) -> None:
        self.assertEqual(self.found["total_reported"], 1, self.found)
        document = self.found["documents"][0]
        self.assertEqual(document["nd"], ND_342, self.found)
        self.assertEqual(document["number"], "342-ФЗ")
        self.assertEqual(document["date"], "03.08.2018")
        self.assertIn("Градостроительный кодекс", document["title"])
        # The publication portal's own identifier appears here too, which is what
        # makes pravo_lookup.py and this module cross-checkable against each other.
        self.assertEqual(document["eo_number"], "0001201808040001", document)

    def test_nonexistent_act_differs_in_kind_from_a_real_one(self) -> None:
        """Not "fewer results" - a different, explicitly flagged outcome."""
        self.assertTrue(self.empty["ok"], self.empty)
        self.assertTrue(self.empty.get("empty_result"), self.empty)
        self.assertEqual(self.empty["returned"], 0, self.empty)
        self.assertEqual(self.empty["documents"], [])
        self.assertNotIn("empty_result", self.found)
        self.assertGreater(self.found["returned"], 0)

    def test_a_parser_returning_nothing_from_a_populated_page_fails_loudly(self) -> None:
        original = ips_lookup._parse_list_item
        ips_lookup._parse_list_item = lambda block: None
        try:
            with self.assertRaises(IpsError):
                find(number="342-ФЗ", date="03.08.2018")
        finally:
            ips_lookup._parse_list_item = original


class RedactionListTest(unittest.TestCase):
    """The list must be complete, and a partial one must not pass as complete."""

    listing: dict
    gk: dict
    apk: dict

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.listing = redactions(ND_342, on_date="10.02.2023")
            cls.gk = redactions(ND_GK1)
            cls.apk = redactions(ND_APK)
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable or errored: {exc}") from exc

    def test_342_fz_history_is_complete_and_current(self) -> None:
        numbers = [entry["amending_act_number"] for entry in self.listing["redactions"]]
        self.assertEqual(numbers[0], None, self.listing)
        self.assertIn("455-ФЗ", numbers)
        self.assertIn("496-ФЗ", numbers, "the amendment this repository missed is absent")
        self.assertEqual(self.listing["current_rdk"], numbers.index("496-ФЗ"), self.listing)

    def test_a_heavily_amended_code_yields_a_long_history_not_a_stub(self) -> None:
        """A comparative check: two acts must not produce interchangeable lists."""
        self.assertGreater(self.gk["count_listed"], 100, self.gk["count_listed"])
        self.assertGreater(self.gk["count_listed"], self.listing["count_listed"] * 10)

    def test_redactions_the_system_has_no_text_for_are_listed_not_dropped(self) -> None:
        """The gap is in the source; hiding it would make a partial list look whole.

        The АПК lists ~90 redactions but has prepared text for far fewer. An
        earlier version of this parser matched only the selectable options and
        silently returned 65 of them as if that were the entire history.
        """
        self.assertGreater(self.apk["count_listed"], self.apk["count_with_text"], self.apk)
        self.assertEqual(
            self.apk["count_listed"] - self.apk["count_with_text"],
            len(self.apk["redactions_without_text"]),
        )
        self.assertIsNotNone(self.apk["text_gap_note"])
        ordinals = [entry["rdk"] for entry in self.apk["redactions"]]
        self.assertEqual(ordinals, sorted(ordinals), "redaction ordinals are not contiguous/ordered")
        self.assertEqual(
            ordinals,
            list(range(len(ordinals))),
            "a gap in the ordinals means listed redactions were dropped",
        )
        # 342-ФЗ, by contrast, has text for every redaction it lists.
        self.assertEqual(self.listing["redactions_without_text"], [])
        self.assertIsNone(self.listing["text_gap_note"])

    def test_on_date_is_returned_as_an_upper_bound_not_as_an_answer(self) -> None:
        bound = self.listing["upper_bound_by_signing_date"]
        self.assertEqual(bound["rdk_at_most"], 3, self.listing)
        self.assertIn("ВЕРХНЯЯ ГРАНИЦА", bound["caveat"])
        self.assertIn("NOT dates of entry into force", self.listing["dates_are"])

    def test_nonexistent_nd_is_an_empty_result_not_a_failure(self) -> None:
        try:
            result = redactions("999999999")
        except IpsError as exc:
            self.fail(f"a nonexistent nd must not be reported as a tool failure: {exc}")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["not_found"], result)

    def test_a_truncated_history_for_an_amended_act_fails_loudly(self) -> None:
        """The inverse check: a short list must never pass as a complete one.

        The page is replayed with every redaction option but the first removed,
        exactly as a broken selector parse would look. The act's own status line
        still says «Действует с изменениями», so the module must refuse it.
        """
        original = ips_lookup._get

        def truncating_get(url: str) -> bytes:
            raw = original(url)
            if "docbody=" not in url:
                return raw
            page = raw.decode(ips_lookup.CHARSET, "replace")
            page = re.sub(
                r"(?is)(<select name=\"doc_editions\".*?</option>).*?(</select>)",
                r"\1\2",
                page,
                count=1,
            )
            return page.encode(ips_lookup.CHARSET, "replace")

        ips_lookup._get = truncating_get
        try:
            with self.assertRaises(IpsError):
                redactions(ND_342)
        finally:
            ips_lookup._get = original


class ConsolidatedTextTest(unittest.TestCase):
    """The point of the whole module: different redactions must differ in text."""

    current: dict
    previous: dict
    original: dict

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.current = document_text(ND_342, rdk=6)
            cls.previous = document_text(ND_342, rdk=5)
            cls.original = document_text(ND_342, rdk=0)
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable or errored: {exc}") from exc

    def test_superscript_article_subnumbers_survive_as_dotted_numbers(self) -> None:
        """"статья 57.1" must not arrive as "статья 571".

        The export carries the superscript on a CSS class, not a <sup> tag, so
        stripping tags naively fuses the digits into an article number that does
        not exist. The damage is not cosmetic: a reader searching the retrieved
        text for "57.1" finds nothing, and nothing reads as "not mentioned"
        while the article is in fact all over the document. Both halves are
        asserted, because restoring the dot is only half the fix if the fused
        form survives somewhere else.
        """
        text = self.current["text"]
        self.assertIn(
            "57.1", text, "the dotted subnumber is missing - superscripts were lost"
        )
        fused = re.findall(r"стать[а-я]{0,2}\s+\d+(?<!\.)(?:571|572|573)\b", text)
        self.assertEqual(
            [], fused, f"fused superscript article numbers survived: {fused[:5]}"
        )

    def test_three_redactions_return_three_different_texts(self) -> None:
        texts = {self.original["text"], self.previous["text"], self.current["text"]}
        self.assertEqual(
            len(texts),
            3,
            "--rdk did not select: two or more redactions returned identical text, "
            "which is the shape of every silent-wrong-answer bug this repo has had",
        )

    def test_the_text_names_its_own_redaction_set(self) -> None:
        """Evidence from the document itself that --rdk took effect."""
        self.assertIn("496-ФЗ", self.current["amendment_note_in_text"] or "")
        self.assertNotIn("496-ФЗ", self.previous["amendment_note_in_text"] or "")
        self.assertIsNone(self.original["amendment_note_in_text"])

    def test_496_fz_rewording_of_part_23_article_26(self) -> None:
        """The decisive proof that the text is genuinely consolidated.

        This exact clause is why this test file exists: a live court position in
        this project rested on ч. 23 ст. 26 of 342-ФЗ in wording that 496-ФЗ had
        already replaced nine months earlier.
        """
        current_23 = part_23(extract_article(self.current["text"], "26") or "")
        previous_23 = part_23(extract_article(self.previous["text"], "26") or "")
        self.assertTrue(current_23, "ч. 23 ст. 26 not located in the current redaction")
        self.assertTrue(previous_23, "ч. 23 ст. 26 not located in redaction 5")
        self.assertIn(WORDING_NEW, current_23)
        self.assertIn("496-ФЗ", current_23)
        self.assertIn(WORDING_OLD, previous_23)
        self.assertNotIn(WORDING_NEW, previous_23)
        self.assertNotEqual(current_23, previous_23)

    def test_stale_and_current_deadlines_are_not_interchangeable(self) -> None:
        """"до 1 января 2028 года" became 2033; both must not appear at once."""
        self.assertIn("2033", self.current["text"])
        self.assertNotIn("2028", self.current["text"])
        self.assertIn("2028", self.previous["text"])
        self.assertNotIn("2033", self.previous["text"])

    def test_missing_article_is_an_empty_result_not_a_failure(self) -> None:
        self.assertIsNotNone(extract_article(self.current["text"], "26"))
        self.assertIsNone(extract_article(self.current["text"], "9999"))

    def test_out_of_range_redaction_fails_loudly(self) -> None:
        with self.assertRaises(IpsError):
            document_text(ND_342, rdk=999)

    def test_unprepared_redaction_is_an_empty_result_not_a_failure(self) -> None:
        """And, critically, not some other redaction's text either."""
        stub = document_text(ND_APK, rdk=RDK_APK_NOT_PREPARED)
        self.assertTrue(stub["ok"], stub)
        self.assertTrue(stub.get("text_not_prepared"), stub)
        self.assertNotIn("text", stub)
        prepared = document_text(ND_APK, rdk=RDK_APK_NOT_PREPARED - 1)
        self.assertNotIn("text_not_prepared", prepared)
        self.assertGreater(len(prepared["text"]), 100000, prepared["chars"])


class TimeoutRetryTest(unittest.TestCase):
    """The retry on timeout, tested without the network.

    Every other test here is live, because the point of this module is what a
    real server returns. This one cannot be: a timeout is exactly the condition
    that cannot be summoned on demand — which is also why this path would
    otherwise ship unexercised and rot. The transport is stubbed instead.
    """

    def _run_with(self, outcomes: list) -> tuple[object, int]:
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            calls["n"] += 1
            outcome = outcomes[min(calls["n"] - 1, len(outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        real_urlopen, real_sleep = ips_lookup.urlopen, ips_lookup.time.sleep
        ips_lookup.urlopen = fake_urlopen
        ips_lookup.time.sleep = lambda _seconds: None
        try:
            try:
                return ips_lookup._get("http://example.invalid/x"), calls["n"]
            except IpsError as exc:
                return exc, calls["n"]
        finally:
            ips_lookup.urlopen, ips_lookup.time.sleep = real_urlopen, real_sleep

    class _Response:
        status = 200

        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            return False

    def test_a_timeout_is_retried_once_and_can_succeed(self) -> None:
        """The whole point: a transient slow export must not end the call."""
        result, attempts = self._run_with(
            [TimeoutError("slow"), self._Response(b"payload")]
        )
        self.assertEqual(b"payload", result, "the retry did not return the second answer")
        self.assertEqual(2, attempts, "expected exactly one retry")

    def test_a_second_timeout_fails_loudly_and_stops(self) -> None:
        """One retry, never a loop - and the failure says the retry happened."""
        result, attempts = self._run_with([TimeoutError("slow")])
        self.assertIsInstance(
            result, IpsError, "a persistent timeout must surface as IpsError, not bare"
        )
        self.assertEqual(2, attempts, "retried more than once - this must not loop")
        self.assertIn("retried once automatically", str(result))


class CommencementTest(unittest.TestCase):
    """Entry-into-force clauses are read from the amending acts themselves.

    The IPS does not carry them: its redaction list gives the signing date of
    each amending act and nothing else. The clause does exist, in the amending
    act's own final article, and this walks there and quotes it. The assertions
    below are about the boundary being real, not about a computed answer — the
    module deliberately computes none.
    """

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.result = ips_lookup.redactions(
                ND_342, on_date="10.02.2023", with_commencement=True
            )
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable: {exc}") from exc

    def test_the_boundary_redaction_and_the_next_one_are_both_read(self) -> None:
        block = self.result.get("commencement") or {}
        self.assertEqual([3, 4], block.get("checked_redactions"))
        self.assertLessEqual(len(block.get("acts", [])), block.get("max_acts", 0))

    def test_the_next_redaction_commenced_after_the_asked_date(self) -> None:
        """What makes the upper bound meaningful rather than arithmetic.

        469-ФЗ was signed 04.08.2023 and commences 01.09.2024 — after
        10.02.2023 either way. If this ever quoted a date before the asked one,
        the bound would be wrong and the tool would be pointing at the wrong
        redaction of the act.
        """
        acts = {a["rdk"]: a for a in (self.result.get("commencement") or {}).get("acts", [])}
        clauses = " ".join(acts[4].get("clauses") or [])
        self.assertIn("2024", clauses, acts[4])
        self.assertTrue(acts[4]["resolved"], acts[4])

    def test_no_in_force_redaction_is_ever_asserted(self) -> None:
        """The one thing this must not do: answer the legal question itself."""
        serialised = json.dumps(self.result, ensure_ascii=False)
        for forbidden in ("rdk_in_force", "in_force_redaction", "applicable_rdk"):
            self.assertNotIn(forbidden, serialised)
        self.assertIn("ВЕРХНЯЯ ГРАНИЦА", serialised)


class DocumentHeadingTest(unittest.TestCase):
    """The result must name the act, not only its nd.

    An nd is not self-checking. A subagent verifying a citation reached for
    102108261 believing it to be 218-ФЗ, got an honest "article not found" from
    152-ФЗ «О персональных данных», and reported that the cited article does not
    exist — a true answer about the wrong act, indistinguishable from a real
    finding. The heading makes that mistake announce itself.
    """

    def test_the_heading_names_the_act(self) -> None:
        try:
            personal_data = ips_lookup.document_text("102108261")
            registration = ips_lookup.document_text("102376335")
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable: {exc}") from exc
        self.assertIn("О персональных данных", personal_data["document_heading"])
        self.assertIn("регистрации недвижимости", registration["document_heading"])
        self.assertNotEqual(
            personal_data["document_heading"], registration["document_heading"]
        )

    def test_the_heading_stops_before_the_body(self) -> None:
        """A stop rule that never fires is how a stray control byte hid here.

        The pattern held a literal 0x08 instead of a word boundary, so nothing
        ever matched and the heading ran on into "Принят Государственной Думой".
        The only symptom was a slightly long string - visible, but easy to read
        past.
        """
        heading = ips_lookup._document_heading(
            "\n".join(
                [
                    "Complex",
                    "РОССИЙСКАЯ ФЕДЕРАЦИЯ",
                    "ФЕДЕРАЛЬНЫЙ ЗАКОН",
                    "О чём-то",
                    "Принят Государственной Думой 8 июля 2006 года",
                    "Статья 1. Начало",
                ]
            )
        )
        self.assertIn("О чём-то", heading)
        self.assertNotIn("Принят", heading)
        self.assertNotIn("Complex", heading)


class ArticleAbsenceTest(unittest.TestCase):
    """«Статья N не найдена» must say which of two things it means.

    In an act that has articles it means the article is not among them: wrong
    act, or repealed. In an act that has none it means the question was put to
    the wrong shape of document — a постановление is divided into пункты, and
    asking it for an article can only ever fail. Both used to come back as the
    same sentence, which is the shape this family keeps catching: an absence
    that reads as "такой нормы не существует".
    """

    ARTICLED = """Статья 1. Первая
Тело первой статьи.
Статья 2
. Вторая
Тело второй статьи.
"""
    CLAUSED = """1. Первый пункт.
2. Второй пункт.
3. Третий пункт.
"""

    def test_the_count_is_the_extractors_own_notion_of_a_heading(self) -> None:
        """A count that disagreed with the extractor would be worse than none."""
        self.assertEqual(2, ips_lookup.count_article_headings(self.ARTICLED))
        self.assertIsNotNone(extract_article(self.ARTICLED, "1"))
        self.assertIsNotNone(extract_article(self.ARTICLED, "2"))
        self.assertEqual(0, ips_lookup.count_article_headings(self.CLAUSED))

    def test_an_act_without_articles_says_so(self) -> None:
        report = ips_lookup.article_absence_report(self.CLAUSED, "5", "ПОСТАНОВЛЕНИЕ", "1")
        self.assertFalse(report["article_found"])
        self.assertFalse(report["act_uses_articles"])
        self.assertEqual(0, report["article_headings_in_act"])
        self.assertIn("пункт", report["note"])

    def test_an_act_with_articles_reports_how_many(self) -> None:
        report = ips_lookup.article_absence_report(self.ARTICLED, "5", "ЗАКОН", "1")
        self.assertTrue(report["act_uses_articles"])
        self.assertEqual(2, report["article_headings_in_act"])

    def test_the_two_absences_do_not_share_a_sentence(self) -> None:
        clauses = ips_lookup.article_absence_report(self.CLAUSED, "5", "ПОСТАНОВЛЕНИЕ", "1")
        articles = ips_lookup.article_absence_report(self.ARTICLED, "5", "ЗАКОН", "1")
        self.assertNotEqual(clauses["note"], articles["note"])


class ArticleFlagOutputTest(unittest.TestCase):
    """`chars` must measure the text that is present, not the one removed."""

    def test_a_clause_only_act_is_a_readable_negative(self) -> None:
        try:
            full = document_text(ND_POSTANOVLENIE)
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable: {exc}") from exc
        self.assertEqual(0, ips_lookup.count_article_headings(full["text"]))
        code, payload = run_text(ND_POSTANOVLENIE, "--article", "5")
        self.assertEqual(1, code)
        self.assertFalse(payload["article_found"])
        self.assertFalse(payload["act_uses_articles"])
        self.assertEqual(0, payload["article_headings_in_act"])
        self.assertNotIn("text", payload)
        self.assertNotIn(
            "chars", payload, "chars kept measuring the text that was removed"
        )
        self.assertEqual(full["chars"], payload["document_chars"])

    def test_a_found_article_reports_its_own_length(self) -> None:
        try:
            code, payload = run_text(ND_342, "--article", "26")
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable: {exc}") from exc
        self.assertEqual(0, code)
        self.assertTrue(payload["article_found"])
        self.assertEqual(len(payload["text"]), payload["chars"])
        self.assertGreater(payload["document_chars"], payload["chars"])

    def test_head_truncation_does_not_leave_a_stale_count(self) -> None:
        try:
            code, payload = run_text(ND_342, "--article", "26", "--head", "500")
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable: {exc}") from exc
        self.assertEqual(0, code)
        self.assertTrue(payload["head_truncated"])
        self.assertEqual(len(payload["text"]), payload["chars"])


class ThisSkillsOwnActTest(unittest.TestCase):
    """One live check that the tool reaches what *this* skill actually cites.

    The mechanism tests above run against 342-ФЗ because it has a verified
    textual difference between two redactions. That proves the tool works; it
    does not prove it reaches patent-ru's own material. This does.
    """

    def test_this_skills_key_act_resolves_and_its_article_extracts(self) -> None:
        try:
            found = ips_lookup.document_text("102110716")
        except IpsError as exc:
            raise unittest.SkipTest(f"pravo.gov.ru IPS unreachable: {exc}") from exc
        body = ips_lookup.extract_article(found["text"], "1363")
        self.assertIsNotNone(
            body, "статья 1363 не извлеклась из ГК РФ часть четвёртая (230-ФЗ)"
        )
        self.assertIn("изобретение", body.lower(), body[:200])
        self.assertLess(
            len(body),
            len(found["text"]) // 2,
            "извлечённая статья занимает половину акта - граница не сработала",
        )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(
        "Running LIVE requests against http://pravo.gov.ru/proxy/ips/ - this is "
        "not a hermetic unit test and needs a working network connection.",
        file=sys.stderr,
    )
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        [
            loader.loadTestsFromTestCase(AddressingTest),
            loader.loadTestsFromTestCase(RedactionListTest),
            loader.loadTestsFromTestCase(ConsolidatedTextTest),
            loader.loadTestsFromTestCase(ThisSkillsOwnActTest),
            loader.loadTestsFromTestCase(TimeoutRetryTest),
            loader.loadTestsFromTestCase(CommencementTest),
            loader.loadTestsFromTestCase(DocumentHeadingTest),
            loader.loadTestsFromTestCase(ArticleAbsenceTest),
            loader.loadTestsFromTestCase(ArticleFlagOutputTest),
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print(
            "\nПРОПУЩЕНО: %d. Пропуск на уровне класса сворачивает все его тесты в "
            "одну строку,\nи прогон всё равно печатает OK — то есть утверждения не "
            "проверялись, а выглядит\nкак успех. Это не зелёный прогон."
            % len(result.skipped),
            file=sys.stderr,
        )
        for case, reason in result.skipped:
            print(f"  {case}: {reason}", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
