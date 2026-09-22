# app/esg/scoring.py -- the ESG calculator's scoring (docs/ESG_SCORING_METHODOLOGY.md).
import pytest

from app.esg import scoring

KPIS = ["A", "B", "C", "D"]


def test_page_kpi_scores_reads_pairs_and_drops_junk():
    parsed = {"kpi_scores": [[1, 32], ["2", "80"], [2, 60], [7, 50], [3, 0], [4, 150], "junk", [1], [4, "x"]]}
    # unknown point, 0 and junk dropped; a repeat keeps its higher score; >100 clamped
    assert scoring.page_kpi_scores(parsed, KPIS) == {"A": 32.0, "B": 80.0, "D": 100.0}
    assert scoring.page_kpi_scores({"kpi_scores": {"3": 45}}, KPIS) == {"C": 45.0}
    assert scoring.page_kpi_scores({"kpi_scores": []}, KPIS) == {}
    assert scoring.page_kpi_scores("not a dict", KPIS) == {}
    # older strong/partial answers read as 100 / 50
    assert scoring.page_kpi_scores({"score": 40, "kpis_strong": [1], "kpis_partial": [2]}, KPIS) == {"A": 100.0, "B": 50.0}


def test_page_score_is_the_average_of_the_page_kpis():
    assert scoring.page_score({"KPI 8": 32, "KPI 9": 80}) == 56
    assert scoring.page_score({"KPI 4": 50, "KPI 9": 65}) == 57.5
    assert scoring.page_score({}) == 0


def test_methodology_example():
    """The worked example in docs/ESG_SCORING_METHODOLOGY.md: KPI 9 is scored 75 on one
    page and 15 (poor performance) on another, so it is held down to 20."""
    kpis = [f"KPI {i}" for i in range(1, 11)]
    pages = [(1, {}), (2, {"KPI 8": 45, "KPI 9": 75}), (7, {"KPI 4": 55, "KPI 9": 15})]
    detail = scoring.category_detail(pages, kpis)
    rows = {r["kpi"]: r for r in detail["kpis"]}
    assert (rows["KPI 4"]["score"], rows["KPI 4"]["pages"]) == (55, [7])
    assert (rows["KPI 8"]["score"], rows["KPI 8"]["pages"]) == (45, [2])
    assert (rows["KPI 9"]["score"], rows["KPI 9"]["pages"]) == (20, [2, 7])
    assert rows["KPI 9"]["capped"] is True and "capped" not in rows["KPI 4"]
    assert (rows["KPI 1"]["score"], rows["KPI 1"]["pages"]) == (0, [])
    # The average of all 10 KPI scores: (55 + 45 + 20) / 1000 * 100
    assert detail["score"] == 12.0
    overall = scoring.composite_score(12.0, 22, 30)
    assert overall == pytest.approx(21.30)
    assert scoring.evaluate_score(overall) == ("D", "Below Average")


def test_poor_performance_caps_the_kpi_but_only_when_it_is_beaten():
    assert scoring.capped_score([75, 15]) == (20.0, True)     # good page, poor page -> 20
    assert scoring.capped_score([15, 8]) == (15.0, False)     # all poor: its own best stands
    assert scoring.capped_score([20, 90]) == (20.0, True)     # 20 is still poor
    assert scoring.capped_score([21, 90]) == (90.0, False)    # 21 is weak, not poor
    assert scoring.capped_score([]) == (0.0, False)
    assert scoring.capped_score([0, 0]) == (0.0, False)


def test_an_analyst_edit_replaces_a_capped_score():
    detail = scoring.category_detail([(2, {"A": 80}), (3, {"A": 10})], ["A", "B"])
    assert detail["kpis"][0]["score"] == 20 and detail["kpis"][0]["capped"] is True
    edited = scoring.rescore_category(detail, {"A": 70})
    assert edited["kpis"][0]["score"] == 70 and "capped" not in edited["kpis"][0]
    assert edited["score"] == 35.0                            # (70 + 0) / 200 * 100


def test_classification_reads_the_categories_and_skips_junk():
    assert scoring.parse_categories({"categories": ["Social", "Environment"]}) == ["Environment", "Social"]
    assert scoring.parse_categories({"categories": ["E", "g"]}) == ["Environment", "Governance"]
    assert scoring.parse_categories({"categories": ["Finance", 7]}) == []
    assert scoring.parse_categories({"categories": []}) == []
    # None, not [] -- an unreadable answer must not silently skip the page
    assert scoring.parse_categories({"other": 1}) is None
    assert scoring.parse_categories("boom") is None
    prompt = scoring.classify_prompt("page {text} with 100% braces")
    assert prompt.endswith("Page:\npage {text} with 100% braces") and '"categories"' in prompt


def test_levels_and_labels():
    assert [scoring.level(s) for s in (0, 1, 60, 61, 100)] == ["none", "partial", "partial", "strong", "strong"]
    assert scoring.kpi_labels({"A": 32.0, "B": 80.5}, KPIS) == ["B (80.5)", "A (32)"]


def test_prompt_guide_goes_before_the_text_and_survives_format():
    prompt = scoring.with_score_guide("Score:\n1. A\n\nText:\n{text}")
    assert prompt.format(text="x").endswith("Text:\nx")
    assert '"kpi_scores"' in prompt and "81-100:" in prompt


def test_the_guide_replaces_the_leftover_page_score_fields():
    """The page "score" is never used and a reason or keyword list about it contradicts the
    KPI marks, so those field lines are dropped from the prompt as it is sent."""
    template = ('Evaluate:\n1. A\n\nProvide the response in JSON format:\n'
                '- "reason": A Detailed explanation of the score.\n'
                '- "score": A number between 0 and 100.\n'
                '- "positive_keywords": A list of keywords that contributed positively.\n'
                '- "negative_keywords": A list of keywords that reduced the score.\n'
                '- "sector": The sector of the company.\n\nText:\n{text}')
    out = scoring.with_score_guide(template)
    assert '- "score": A number between 0 and 100.' not in out
    assert "A Detailed explanation of the score" not in out
    assert "contributed positively" not in out
    assert '- "sector": The sector of the company.' in out        # untouched
    assert '- "reason": One line for each point you scored' in out
    assert out.endswith("Text:\n{text}")


def test_negative_keywords_without_a_poor_score_are_flagged_for_review():
    answer = {"negative_keywords": ["environmental fine", "spill"]}
    note = scoring.contradiction(answer, {"A": 70.0, "B": 45.0})
    assert note == ("Check: negative keywords (environmental fine, spill) but no KPI on this "
                    "page scored 1-20")
    # A poor score on the page agrees with the keywords: nothing to check.
    assert scoring.contradiction(answer, {"A": 70.0, "B": 12.0}) == ""
    assert scoring.contradiction({"negative_keywords": []}, {"A": 70.0}) == ""
    assert scoring.contradiction(answer, {}) == "" and scoring.contradiction("boom", {"A": 70.0}) == ""


# --- why a KPI scored what it scored (the Reason column) -- user, 2026-09-20 ---------

def _entries(*rows):
    """(score, page, reason) as category_detail collects them."""
    return list(rows)


def test_evidence_keeps_the_other_pages_that_scored_the_kpi():
    ev = scoring._evidence(_entries((85, 11, "down 22% vs a 2030 target"),
                                    (61, 52, "renewables at 31%"),
                                    (40, 60, "policy only")), capped=False)
    assert ev["page"] == 11 and ev["score"] == 85
    assert [a["page"] for a in ev["also"]] == [52, 60]
    assert ev["also"][0]["reason"] == "renewables at 31%"


def test_evidence_of_a_capped_kpi_leads_with_the_poor_page():
    """The score came from the poor page, so that is the reason; the good pages follow,
    which is what explains a 20 sitting under a list of ten pages."""
    ev = scoring._evidence(_entries((85, 11, "down 22%"), (15, 57, "missed the interim target")),
                           capped=True)
    assert (ev["page"], ev["score"]) == (57, 15)
    assert ev["also"] == [{"page": 11, "score": 85, "reason": "down 22%"}]


def test_evidence_keeps_at_most_two_supporting_pages():
    ev = scoring._evidence(_entries(*[(90 - i, i, f"reason {i}") for i in range(1, 8)]), capped=False)
    assert len(ev["also"]) == scoring.EVIDENCE_ALSO == 2


def test_evidence_skips_pages_the_answer_gave_no_reason_for():
    ev = scoring._evidence(_entries((85, 11, "down 22%"), (61, 52, ""), (40, 60, "policy only")),
                           capped=False)
    assert [a["page"] for a in ev["also"]] == [60]


def test_a_kpi_that_scored_nowhere_has_no_evidence():
    assert scoring._evidence(_entries((0, 3, "only mentioned")), capped=False) == {}


def test_category_detail_carries_the_evidence_onto_the_kpi_row():
    detail = scoring.category_detail(
        [(11, {"Water withdrawn": 85}, {"Water withdrawn": "down 22%"}),
         (52, {"Water withdrawn": 61}, {"Water withdrawn": "recycling at 31%"})],
        ["Water withdrawn"],
    )
    ev = detail["kpis"][0]["evidence"]
    assert ev["reason"] == "down 22%" and ev["also"][0]["page"] == 52


# --- the answer's reason lines, however they arrive (production, 2026-09-21) ----------

REASON_LINES = ["16 60: 24 average training hours per employee",
                "123 70: Rs. 177.3 Bn local procurement spend, up 19%"]
NUMBERED_KPIS = [f"KPI {i}" for i in range(1, 131)]


def test_reason_lines_are_read_when_the_answer_returns_them_as_a_list():
    """Production answers with a list of lines, not one string. Read as a string it was
    dropped whole, which left every KPI in every report with no reason at all."""
    out = scoring.kpi_reasons({"reason": REASON_LINES}, NUMBERED_KPIS)
    assert out["KPI 16"] == "24 average training hours per employee"
    assert out["KPI 123"] == "Rs. 177.3 Bn local procurement spend, up 19%"


def test_reason_lines_are_still_read_as_one_string():
    out = scoring.kpi_reasons({"reason": "\n".join(REASON_LINES)}, NUMBERED_KPIS)
    assert out["KPI 16"] == "24 average training hours per employee"


def test_a_list_of_reasons_joins_into_readable_text():
    """The page-scores export printed str(list) -- the raw Python literal -- as the page's
    reason. It gets the lines instead."""
    text = scoring.reason_text({"reason": REASON_LINES})
    assert text == "\n".join(REASON_LINES)
    assert "[" not in text and "'" not in text


@pytest.mark.parametrize("value", [None, 42, {"a": 1}, [], ["", "  "]])
def test_no_reason_is_no_reason(value):
    assert scoring.reason_text({"reason": value}) == ""
    assert scoring.kpi_reasons({"reason": value}, NUMBERED_KPIS) == {}


def test_a_listed_reason_reaches_the_kpi_row_as_evidence():
    """End to end: the shape production returns now lands in the report's Reason column."""
    detail = scoring.category_detail(
        [(12, {"KPI 16": 60}, scoring.kpi_reasons({"reason": REASON_LINES}, NUMBERED_KPIS))], NUMBERED_KPIS)
    row = next(r for r in detail["kpis"] if r["kpi"] == "KPI 16")
    assert row["evidence"]["reason"] == "24 average training hours per employee"
    assert row["evidence"]["page"] == 12
