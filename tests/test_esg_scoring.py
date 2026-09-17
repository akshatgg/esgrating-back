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
    """The worked example in docs/ESG_SCORING_METHODOLOGY.md."""
    kpis = [f"KPI {i}" for i in range(1, 11)]
    pages = [(1, {"KPI 4": 32}), (2, {"KPI 8": 32, "KPI 9": 80}), (7, {"KPI 4": 50, "KPI 9": 65})]
    detail = scoring.category_detail(pages, kpis)
    best = {r["kpi"]: (r["score"], r["pages"]) for r in detail["kpis"]}
    assert best["KPI 4"] == (50, [1, 7]) and best["KPI 8"] == (32, [2]) and best["KPI 9"] == (80, [2, 7])
    assert best["KPI 1"] == (0, [])
    assert detail["score"] == 16.2
    overall = scoring.composite_score(16.2, 55, 70)
    assert overall == pytest.approx(46.67)
    assert scoring.evaluate_score(overall) == ("C", "Average")


def test_levels_and_labels():
    assert [scoring.level(s) for s in (0, 1, 60, 61, 100)] == ["none", "partial", "partial", "strong", "strong"]
    assert scoring.kpi_labels({"A": 32.0, "B": 80.5}, KPIS) == ["B (80.5)", "A (32)"]


def test_prompt_guide_goes_before_the_text_and_survives_format():
    prompt = scoring.with_score_guide("Score:\n1. A\n\nText:\n{text}")
    assert prompt.format(text="x").endswith("Text:\nx")
    assert '"kpi_scores"' in prompt and "81-100:" in prompt


def test_weights_new_and_legacy():
    assert scoring.composite_score(100, 0, 0) == pytest.approx(35)
    assert scoring.weights_for({"scoring_method": "kpi_score"}) == scoring.WEIGHTS
    assert scoring.weights_for({}) == scoring.LEGACY_WEIGHTS
    assert scoring.composite_score(100, 0, 0, scoring.weights_for({})) == pytest.approx(30)


def test_summary_rows():
    detail = scoring.category_detail([(1, {"A": 50}), (7, {"A": 40, "B": 80})], ["A", "B"])
    final = {"scoring_method": "kpi_score", "kpi_coverage": {"Environment": detail},
             "environmental_score": 65, "composite_score": 46.666, "composite_score_performance": "C"}
    assert scoring.summary_rows(final) == [
        [],
        ["Category", "KPI", "Best Score", "Found on Pages"],
        ["Environment", "A", "50", "1, 7"],
        ["Environment", "B", "80", "7"],
        ["Environment total", "", "65", ""],
        ["Overall", "35% Environment + 30% Social + 35% Governance", "46.67", "Grade C"],
    ]
    assert scoring.summary_rows({}) == []
