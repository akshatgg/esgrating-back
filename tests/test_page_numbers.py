"""The number a report cites for a page, and the page number in the Word footer.

A page is held internally by its SHEET -- what the PDF counts from the cover. The number
printed on the page starts after the front matter, so the two run a constant apart, and
every citation read that many pages ahead of where the reader looked (user, 2026-09-29).
"""
import docx
from docx.oxml.ns import qn

from app.esg import extract
from app.reports import summary


def _sheet(no, *lines):
    return {"page_no": no, "text": "\n".join(lines)}


def _report(sheets, offset, folio_on_every_page=True):
    """A document of `sheets` pages whose printed folio runs `offset` behind the sheet."""
    pages = []
    for n in range(1, sheets + 1):
        printed = n - offset
        body = [f"Some narrative text on this page about emissions and water."]
        if printed >= 1 and folio_on_every_page:
            body.append(str(printed))
        pages.append(_sheet(n, *body))
    return pages


# --- reading the printed number off the page ---------------------------------------------

def test_a_constant_front_matter_offset_is_measured():
    assert extract.folio_offset(_report(40, offset=2)) == 2


def test_a_report_that_starts_numbering_at_its_first_sheet_has_no_offset():
    assert extract.folio_offset(_report(40, offset=0)) == 0


def test_front_matter_has_no_printed_number_to_cite():
    pages = extract.number_pages(_report(40, offset=2))
    assert pages[0]["printed_no"] is None       # the cover
    assert pages[1]["printed_no"] is None
    assert pages[2]["printed_no"] == 1          # sheet 3 is the page printed 1
    assert pages[33]["printed_no"] == 32        # the user's example: sheet 34 -> 32


def test_a_document_whose_folios_do_not_extract_is_left_on_sheet_numbers():
    """An image-only scan or a deck: nothing readable, so nothing is invented.

    None, not 0. A zero offset would say "the folios were read and they match the sheets",
    and every page would then be cited as a page number nobody ever saw."""
    pages = [_sheet(n, "text with no standalone number") for n in range(1, 40)]
    assert extract.folio_offset(pages) is None
    assert extract.number_pages(pages)[10]["printed_no"] is None


def test_an_unreadable_document_is_not_confused_with_one_numbered_from_its_first_sheet():
    read = _report(40, offset=0)
    unread = [_sheet(n, "text with no standalone number") for n in range(1, 40)]
    assert extract.folio_offset(read) == 0 and extract.folio_offset(unread) is None
    assert extract.number_pages(read)[10]["printed_no"] == 11
    assert extract.number_pages(unread)[10]["printed_no"] is None


def test_stray_numbers_in_tables_do_not_outvote_the_real_numbering():
    """Every page carries a table of figures near its own sheet number. The real folio is on
    every page and wins; the noise scatters across offsets."""
    pages = _report(60, offset=3)
    for i, page in enumerate(pages):
        page["text"] += f"\n{i + 7}\n{i + 11}\n"   # two decoys, each a consistent offset too
    assert extract.folio_offset(pages) == 3


def test_a_folio_further_away_than_any_front_matter_is_not_believed():
    pages = [_sheet(n, "text", str(n - 300)) for n in range(301, 340)]
    assert extract.folio_offset(pages) is None


def test_too_few_pages_carry_a_folio_to_call_it_a_numbering_scheme():
    pages = [_sheet(n, "text") for n in range(1, 40)]
    for n in (5, 6):
        pages[n]["text"] += f"\n{n - 1}\n"
    assert extract.folio_offset(pages) is None


# --- how a page is named to the reader ----------------------------------------------------

def test_a_page_is_cited_by_the_number_printed_on_it():
    assert summary._page_label(34, {"34": 32}) == "32"


def test_a_page_with_no_printed_number_is_named_as_a_sheet():
    """Never passed off as a page number: the reader would look in the wrong place, which is
    the whole complaint."""
    assert summary._page_label(2, {"34": 32}) == "PDF sheet 2"
    assert summary._page_label(34, {}) == "PDF sheet 34"
    assert summary._page_label(34, None) == "PDF sheet 34"


def test_the_source_pages_column_uses_the_printed_numbers():
    assert summary._pages([34, 35], {"34": 32, "35": 33}) == "Found on p. 32, 33"


def test_source_pages_never_mixes_the_two_numbering_schemes():
    """One list carrying "32, PDF sheet 2" is exactly the confusion this fixes. If any page
    in the list has no folio, the whole list is given as sheets."""
    assert summary._pages([2, 34], {"34": 32}) == "Found on PDF sheet 2, 34"


def test_source_pages_falls_back_to_sheets_on_a_report_scored_before_this_existed():
    assert summary._pages([3], {}) == "Found on PDF sheet 3"


def test_kpi_evidence_quotes_the_printed_page():
    row = {"kpi": "Water withdrawn", "score": 90,
           "evidence": {"page": 34, "reason": "22% reduction against a 2030 target"}}
    assert "p.32:" in summary._kpi_evidence(row, {"34": 32})


def test_kpi_evidence_falls_back_to_the_sheet_when_the_folio_is_unknown():
    row = {"kpi": "Water withdrawn", "score": 90,
           "evidence": {"page": 34, "reason": "22% reduction"}}
    assert "PDF sheet 34:" in summary._kpi_evidence(row, {})


# --- the page number in the Word footer ---------------------------------------------------

def _footer_xml(document):
    return document.sections[0].footer.paragraphs[0]._p.xml


def test_the_footer_carries_page_and_numpages_fields():
    """Word evaluates these itself while laying the document out, so the number cannot drift
    from the document the way one we counted ourselves would."""
    d = docx.Document()
    d.sections[0].footer.paragraphs[0].text = summary._FOOTER_ANCHOR
    summary._number_pages(d)
    xml = _footer_xml(d)
    assert " PAGE " in xml and " NUMPAGES " in xml
    assert summary._FOOTER_ANCHOR in d.sections[0].footer.paragraphs[0].text


def test_the_fields_are_marked_dirty_so_word_evaluates_them_on_open():
    d = docx.Document()
    d.sections[0].footer.paragraphs[0].text = summary._FOOTER_ANCHOR
    summary._number_pages(d)
    assert 'w:dirty="true"' in _footer_xml(d)


def test_numbering_a_footer_twice_does_not_double_it():
    d = docx.Document()
    d.sections[0].footer.paragraphs[0].text = summary._FOOTER_ANCHOR
    summary._number_pages(d)
    summary._number_pages(d)
    assert _footer_xml(d).count(" PAGE ") == 1


def test_the_page_number_is_appended_to_the_classification_line():
    """One centred footer line, not a second paragraph under it."""
    d = docx.Document()
    footer = d.sections[0].footer
    footer.paragraphs[0].text = summary._FOOTER_ANCHOR
    summary._number_pages(d)
    assert len(footer.paragraphs) == 1


# --- relabelling a citation must not throw away a stored narrative -----------------------

def test_relabelling_citations_keeps_the_stored_narrative():
    """The narrative is cached against a fingerprint of the prompt. Correcting citations to
    the printed page changed every prompt, which marked every stored narrative stale and
    blanked the strengths, weaknesses, priorities and rationale on every existing report
    (user, 2026-09-30). How a page is LABELLED is not part of the rating."""
    legacy = 'Rating data (JSON):\n{"page_reasons": ["Environment p.34: because"]}'
    relabelled = 'Rating data (JSON):\n{"page_reasons": ["Environment PDF sheet 34: because"]}'
    assert summary._fingerprint(legacy) == summary._fingerprint(relabelled)


def test_a_genuinely_different_page_still_rewrites_the_narrative():
    """Normalising the label must not blind the fingerprint: once the printed number is
    known, prose quoting the old one is out of date and is rewritten."""
    before = 'Rating data (JSON):\n{"page_reasons": ["Environment p.34: because"]}'
    after = 'Rating data (JSON):\n{"page_reasons": ["Environment p.32: because"]}'
    assert summary._fingerprint(before) != summary._fingerprint(after)
