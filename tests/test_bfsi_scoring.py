import pytest

from app.bfsi import options
from app.bfsi.scoring import bfsi_grade, bfsi_overall, bfsi_recommendation


@pytest.mark.parametrize("score,grade,label", [
    (90.01, "A+", "Outstanding"), (90, "A", "Excellent"), (80, "A", "Excellent"),
    (79.99, "B+", "Very Good"), (71, "B+", "Very Good"), (70.5, "B", "Good"),
    (61, "B", "Good"), (60.99, "C", "Average"), (40, "C", "Average"), (39.99, "D", "Below Average"),
])
def test_grade_ladder(score, grade, label):
    assert bfsi_grade(score) == {"grade": grade, "label": label}


def test_overall_uses_loan_type_row():
    r = bfsi_overall(80, 60, 70, "Agriculture Loan")          # Agriculture 50/25/25
    assert r["weightage_row"] == "Agriculture" and r["overall"] == round((50*80 + 25*60 + 25*70) / 100, 2)
    assert bfsi_overall(80, 60, 70, "Unknown Type")["weightage_row"] == "Working Capital"


def test_every_loan_type_maps_to_a_weight_row():
    assert len(options.LOAN_TYPES) == 20 and len(options.WEIGHTAGE) == 14
    for lt in options.LOAN_TYPES:
        w = options.WEIGHTAGE[options.LOANTYPE_TO_WEIGHTAGE[lt]]
        assert w["e"] + w["s"] + w["g"] == 100


def test_recommendation():
    assert bfsi_recommendation("A") == "Favorable — low ESG credit risk"
    assert bfsi_recommendation("B+") == "Moderate — lend with standard ESG conditions"
    assert bfsi_recommendation("C") == "Caution — enhanced ESG due diligence recommended"
    assert bfsi_recommendation("D") == "High risk — detailed review before lending"


def test_industries_verbatim_counts():
    assert list(options.INDUSTRIES) == ["manufacturing", "construction", "agriculture", "renewable", "infrastructure",
                                        "realestate", "vehicle", "it", "healthcare", "education", "trade", "services"]
    assert options.INDUSTRIES["it"]["sub_sectors"] == ["Software Services", "SaaS", "BPO/KPO", "Hardware", "Fintech", "Other IT"]
