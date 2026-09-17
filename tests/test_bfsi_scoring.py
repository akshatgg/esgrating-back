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


# The tests below assert the full ported config against docs/analysis/bfsi.md §4d
# (BFSI_LOAN_PURPOSES / BFSI_WEIGHTAGE / BFSI_LOANTYPE_TO_WEIGHTAGE / BFSI_INDUSTRIES),
# not just spot-checked fields, so a wrong ported value would be caught.

def test_loan_purposes_full_list():
    assert options.LOAN_PURPOSES == [
        "Capacity Expansion", "Working Capital", "Land Purchase", "Building Construction",
        "Plant & Machinery", "Renewable Energy", "Warehouse", "New Manufacturing Unit",
        "Acquisition", "Refinancing", "General Corporate Purpose",
    ]


def test_weightage_full_dict():
    expected = {
        "Working Capital": {"e": 20, "s": 30, "g": 50},
        "MSME Loan": {"e": 25, "s": 30, "g": 45},
        "Manufacturing": {"e": 45, "s": 25, "g": 30},
        "Infrastructure": {"e": 45, "s": 25, "g": 30},
        "Construction": {"e": 45, "s": 30, "g": 25},
        "Renewable Energy": {"e": 50, "s": 20, "g": 30},
        "Agriculture": {"e": 50, "s": 25, "g": 25},
        "Vehicle Finance": {"e": 35, "s": 25, "g": 40},
        "Commercial Real Estate": {"e": 40, "s": 25, "g": 35},
        "Trade Finance": {"e": 20, "s": 25, "g": 55},
        "Export Finance": {"e": 25, "s": 30, "g": 45},
        "Healthcare": {"e": 30, "s": 40, "g": 30},
        "Education": {"e": 20, "s": 50, "g": 30},
        "Technology / IT": {"e": 15, "s": 35, "g": 50},
    }
    assert options.WEIGHTAGE == expected


def test_loantype_to_weightage_full_dict():
    expected = {
        "Working Capital": "Working Capital",
        "Term Loan": "Working Capital",
        "Project Finance": "Infrastructure",
        "Machinery Loan": "Manufacturing",
        "Equipment Finance": "Manufacturing",
        "Commercial Vehicle Loan": "Vehicle Finance",
        "Construction Finance": "Construction",
        "Infrastructure Finance": "Infrastructure",
        "Renewable Energy Loan": "Renewable Energy",
        "MSME Loan": "MSME Loan",
        "Agriculture Loan": "Agriculture",
        "Home Loan (Builder)": "Commercial Real Estate",
        "Real Estate Finance": "Commercial Real Estate",
        "Supply Chain Finance": "Trade Finance",
        "Trade Finance": "Trade Finance",
        "Export Finance": "Export Finance",
        "Gold Loan (Corporate)": "Working Capital",
        "Lease Finance": "Working Capital",
        "Venture Debt": "Technology / IT",
        "Bridge Finance": "Working Capital",
    }
    assert options.LOANTYPE_TO_WEIGHTAGE == expected


def test_industries_full_equality():
    expected = {
        "manufacturing": {
            "label": "Manufacturing", "step3_set": "manufacturing",
            "sub_sectors": ["Textiles", "Chemicals", "Auto Components", "Steel & Metals", "Cement",
                            "Pharmaceuticals Mfg", "Electronics", "FMCG", "Other Manufacturing"],
        },
        "construction": {
            "label": "Construction", "step3_set": "construction",
            "sub_sectors": ["Residential", "Commercial", "Industrial", "EPC Contractor", "Other Construction"],
        },
        "agriculture": {
            "label": "Agriculture", "step3_set": "agriculture",
            "sub_sectors": ["Crops", "Dairy", "Poultry", "Agro-processing", "Fisheries", "Other Agriculture"],
        },
        "renewable": {
            "label": "Renewable Energy", "step3_set": "renewable",
            "sub_sectors": ["Solar", "Wind", "Hydro", "Bioenergy", "Storage", "Other Renewable"],
        },
        "infrastructure": {
            "label": "Infrastructure", "step3_set": "construction",
            "sub_sectors": ["Roads", "Ports", "Airports", "Power T&D", "Water", "Urban Infra"],
        },
        "realestate": {
            "label": "Commercial Real Estate", "step3_set": "realestate",
            "sub_sectors": ["Office", "Retail", "Warehousing", "Hospitality", "Mixed Use"],
        },
        "vehicle": {
            "label": "Vehicle / Auto", "step3_set": "vehicle",
            "sub_sectors": ["Fleet Operator", "Logistics", "Passenger Transport", "Auto Dealer", "Other Vehicle"],
        },
        "it": {
            "label": "IT & Technology", "step3_set": "it",
            "sub_sectors": ["Software Services", "SaaS", "BPO/KPO", "Hardware", "Fintech", "Other IT"],
        },
        "healthcare": {
            "label": "Healthcare", "step3_set": "working",
            "sub_sectors": ["Hospitals", "Diagnostics", "Pharma Retail", "Medical Devices", "Other Healthcare"],
        },
        "education": {
            "label": "Education", "step3_set": "working",
            "sub_sectors": ["K-12", "Higher Education", "EdTech", "Vocational", "Other Education"],
        },
        "trade": {
            "label": "Trade & Export", "step3_set": "working",
            "sub_sectors": ["Wholesale", "Retail", "Import/Export", "Commodities", "Other Trade"],
        },
        "services": {
            "label": "Services / MSME", "step3_set": "working",
            "sub_sectors": ["Professional Services", "Financial Services", "Hospitality Services", "Media", "Other Services"],
        },
    }
    assert options.INDUSTRIES == expected
    # Easy-to-miss cases called out in review: infrastructure's step3_set is "construction"
    # (not "infrastructure"), and healthcare/education/trade/services all share "working".
    assert options.INDUSTRIES["infrastructure"]["step3_set"] == "construction"
    for key in ("healthcare", "education", "trade", "services"):
        assert options.INDUSTRIES[key]["step3_set"] == "working"


# --- KPI scoring (docs/BFSI_SCORING_METHODOLOGY.md) -------------------------------------

@pytest.mark.parametrize("score,whole,grade", [
    (90.5, False, "A+"), (90.5, True, "A"), (79.9, True, "B+"), (70.99, True, "B"), (60.9, True, "C"),
])
def test_kpi_scored_reports_grade_on_whole_numbers(score, whole, grade):
    assert bfsi_grade(score, whole)["grade"] == grade


def test_methodology_example_two_loan_types():
    """The worked example in docs/BFSI_SCORING_METHODOLOGY.md."""
    wc = bfsi_overall(16.2, 55, 70, "Working Capital", True)
    assert wc["overall"] == 54.74 and wc["grade"] == "C"
    assert bfsi_recommendation(wc["grade"]) == "Caution — enhanced ESG due diligence recommended"
    ag = bfsi_overall(16.2, 55, 70, "Agriculture Loan", True)
    assert ag["overall"] == 39.35 and ag["grade"] == "D"
    assert bfsi_recommendation(ag["grade"]) == "High risk — detailed review before lending"


def test_summary_rows_use_the_loan_type_weights():
    from app.bfsi import scoring
    from app.esg.scoring import category_detail
    ai = {"scoring_method": "kpi_score", "kpi_coverage": {
        "Environment": category_detail([(1, {"A": 50}), (2, {"A": 40, "B": 80})], ["A", "B"])}}
    rows = scoring.summary_rows(ai, 65, 55, 70, "Renewable Energy Loan")
    assert rows[1:4] == [["Category", "KPI", "Best Score", "Found on Pages"],
                         ["Environment", "A", "50", "1, 2"], ["Environment", "B", "80", "2"]]
    assert rows[-1] == ["Overall", "Renewable Energy: 50% Environment + 20% Social + 30% Governance",
                        "64.50", "Grade B"]
