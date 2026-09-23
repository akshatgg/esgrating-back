"""The CFC ESG Rating Methodology v1.0 chain, checked against the document's own numbers.

Every worked example the methodology gives is a test here, so the arithmetic can be shown
to match the published method rather than merely to run.
"""
import pytest

from app.esg import methodology as m
from app.esg import scoring


# --- 8.3 Indicator Weighting ------------------------------------------------------------

def test_evidence_weights_are_the_documents_six_levels():
    assert m.evidence_weight("Supporting Evidence") == 0.75      # Compliance / Basic
    assert m.evidence_weight("Policy/Strategy") == 1.00
    assert m.evidence_weight("Implementation") == 1.20
    assert m.evidence_weight("Target") == 1.25
    assert m.evidence_weight("Commitment") == 1.25               # Target / Commitment
    assert m.evidence_weight("Measured Performance") == 1.50
    assert m.evidence_weight("Verified Outcome") == 1.60         # Assured / Verified
    # An adverse outcome is still measured performance, not a weaker kind of evidence.
    assert m.evidence_weight("Negative Performance") == 1.50
    # An unrecognised type must not silently become the strongest or the weakest.
    assert m.evidence_weight("something new") == 1.00
    assert m.evidence_weight(None) == 1.00


# --- 7.2 Materiality Levels -------------------------------------------------------------

def test_materiality_weights_are_the_documents_five_levels():
    assert m.MATERIALITY_WEIGHTS == {"signature": 2.00, "high": 1.50, "moderate": 1.00,
                                     "low": 0.50, "not applicable": 0.00}


def test_not_applicable_excludes_an_indicator_instead_of_scoring_it_zero():
    """7.2: Not Applicable carries 0.00x and is 'excluded from scoring'. That is the whole
    difference from a KPI that was simply not found, which does score zero."""
    assert m.indicator_weight("Policy/Strategy", "not applicable") == 0.0
    scored = [{"score": 80, "theme": "Water", "key_issue": "Water I", "evidence_type": "Target",
               "materiality": "moderate"},
              {"score": 0, "theme": "Land", "key_issue": "Land I", "evidence_type": "Policy/Strategy",
               "materiality": "not applicable"}]
    out = m.aggregate(scored)
    assert out["score"] == 80.0 and out["excluded"] == 1 and out["applicable"] == 1


# --- Annexure A. Sector Materiality Matrix ----------------------------------------------

def test_the_sector_matrix_is_the_twenty_cse_industry_groups():
    assert len(m.SECTOR_MATERIALITY) == 20
    assert m.SECTOR_MATERIALITY["Banks"][:3] == ("moderate", "high", "signature")
    assert m.SECTOR_MATERIALITY["Energy"][:3] == ("signature", "high", "high")
    assert m.SECTOR_MATERIALITY["Materials"][:3] == ("signature", "signature", "high")
    assert m.SECTOR_MATERIALITY["Utilities"][:3] == ("signature", "high", "signature")
    levels = {lvl for row in m.SECTOR_MATERIALITY.values() for lvl in row[:3]}
    assert levels <= set(m.MATERIALITY_WEIGHTS)


@pytest.mark.parametrize("reported, expected", [
    ("Banks", "Banks"), ("Banking", "Banks"), ("commercial bank", "Banks"),
    ("Cement", "Materials"), ("Oil and Gas", "Energy"),
    ("Pharmaceuticals", "Pharma / Biotech / Life Sciences"),
    ("Information Technology", "Commercial & Professional Services"),
    ("hotels and leisure", "Consumer Services"),
])
def test_the_sector_the_ai_reports_is_matched_to_the_matrix(reported, expected):
    assert m.sector_profile(reported)[0] == expected


def test_an_unknown_sector_leaves_every_indicator_at_the_default():
    """A sector nobody recognises must not silently reweight a rating."""
    assert m.sector_profile("zzz unknown") is None
    assert m.sector_profile("") is None
    assert m.materiality_of("Emission", "zzz unknown", "Environment") == "moderate"


def test_a_signature_topic_for_the_sector_raises_that_indicator():
    """Annexure A's Signature topics are what actually varies inside a pillar, so they are
    where materiality bites: emissions are Signature for Materials, training is not."""
    assert m.materiality_of("Emission", "Materials", "Environment") == "signature"
    assert m.materiality_of("Training & Development", "Materials", "Social") == "moderate"
    assert m.materiality_of("Cyber Security", "Banks", "Governance") == "signature"


# --- 8.2.7 steps 2-5: Weighted Indicator -> Key Issue -> Theme -> Pillar -----------------

def test_key_issues_and_themes_aggregate_by_weight_not_by_a_flat_mean():
    """A Theme built of more material, better-evidenced indicators carries more of the
    pillar than one built of a single policy mention."""
    indicators = [
        # Water: one measured (1.50x) and one policy (1.00x), same Key Issue.
        {"score": 90, "theme": "Water", "key_issue": "Water I", "evidence_type": "Measured Performance",
         "materiality": "moderate"},
        {"score": 40, "theme": "Water", "key_issue": "Water I", "evidence_type": "Policy/Strategy",
         "materiality": "moderate"},
        # Waste: a single policy mention.
        {"score": 20, "theme": "Waste", "key_issue": "Waste I", "evidence_type": "Policy/Strategy",
         "materiality": "moderate"},
    ]
    out = m.aggregate(indicators)
    water = next(t for t in out["themes"] if t["theme"] == "Water")
    # (90*1.5 + 40*1.0) / 2.5
    assert water["score"] == 70.0 and water["weight"] == 2.5
    # The pillar is the weighted mean of its themes: (70*2.5 + 20*1.0) / 3.5
    assert out["score"] == pytest.approx(55.71, abs=0.01)
    # A flat mean of the three indicators would have been 50.0, and of the two themes 45.0.
    assert out["score"] not in (50.0, 45.0)


def test_a_pillar_with_nothing_applicable_scores_none_rather_than_zero():
    out = m.aggregate([{"score": 0, "theme": "T", "key_issue": "K",
                        "evidence_type": "Policy/Strategy", "materiality": "not applicable"}])
    assert out["score"] is None and out["themes"] == [] and out["excluded"] == 1


# --- 8.2.2 Forward-Looking / Transition Adjustment ---------------------------------------

def test_the_transition_worked_example():
    """The document's own example: 80/70/75/65/80 weighted 20/25/25/15/15 -> 74.0 -> +1.0"""
    out = m.transition({"target_credibility": 80, "target_progress": 70,
                        "transition_readiness": 75, "resilience": 65,
                        "emerging_risk_preparedness": 80})
    assert out["score"] == 74.0 and out["adjustment"] == 1.0


@pytest.mark.parametrize("score, adjustment", [(100, 2.0), (80, 2.0), (79, 1.0), (65, 1.0),
                                               (64, 0.0), (50, 0.0), (49, -1.0), (35, -1.0),
                                               (34, -2.0), (0, -2.0)])
def test_every_transition_band_edge(score, adjustment):
    flat = {k: score for k in m.TRANSITION_WEIGHTS}
    assert m.transition(flat)["adjustment"] == adjustment


def test_an_unassessed_transition_moves_the_score_by_nothing():
    """An area nobody assessed must not read as zero and take 2 points off the rating."""
    assert m.transition(None) == {"score": None, "adjustment": 0.0}
    assert m.transition({}) == {"score": None, "adjustment": 0.0}


def test_a_partly_assessed_transition_uses_only_what_was_assessed():
    out = m.transition({"target_credibility": 80, "target_progress": 80})
    assert out["score"] == 80.0 and out["adjustment"] == 2.0


# --- 8.2.3 Controversy Adjustment --------------------------------------------------------

def test_the_controversy_worked_example():
    """The document's own example: 40/50/60/30/70/80 weighted 20/20/10/15/15/20 -> 55 -> -1.0"""
    out = m.controversy({"severity": 40, "scale_of_impact": 50, "duration": 60,
                         "recurrence": 30, "management_response": 70, "remediation": 80})
    assert out["score"] == 55.0 and out["adjustment"] == -1.0


@pytest.mark.parametrize("score, adjustment", [(100, 0.0), (80, 0.0), (79, -0.5), (65, -0.5),
                                               (64, -1.0), (50, -1.0), (49, -2.0), (35, -2.0),
                                               (34, -3.0), (0, -3.0)])
def test_every_controversy_band_edge(score, adjustment):
    flat = {k: score for k in m.CONTROVERSY_WEIGHTS}
    assert m.controversy(flat)["adjustment"] == adjustment


def test_controversy_is_never_an_upward_adjustment():
    assert max(a for _s, a in m.CONTROVERSY_BANDS) == 0.0


def test_no_controversy_assessed_costs_nothing():
    assert m.controversy(None) == {"score": None, "adjustment": 0.0}


# --- 8.2.4 Final ESG Score, and the whole chain -----------------------------------------

def test_the_documents_end_to_end_worked_example():
    """8.2.7 and 8.2.4: E 72, S 68, G 80, +1.00 transition, -1.00 controversy.

    The document's example states this base as 74.60, but its own formula gives 73.60:
    (72 x 35%) + (68 x 30%) + (80 x 35%) = 25.2 + 20.4 + 28.0 = 73.60. 74.60 is then carried
    through 8.2.2, 8.2.3 and 8.2.4. The formula is implemented, not the stated total -- this
    is an erratum to raise with the methodology owner, and the grade is B+ either way.
    """
    base = scoring.composite_score(72, 68, 80, scoring.WEIGHTS)
    assert base == pytest.approx(73.60)
    final = m.final_score(base, 1.00, -1.00)
    assert final == pytest.approx(73.60)
    assert scoring.evaluate_score(final) == ("B+", "Very Good")


def test_the_maximum_adjustment_example():
    """8.2.4: +2.00 transition and -3.00 controversy take a point off the base."""
    assert m.final_score(73.60, 2.00, -3.00) == pytest.approx(72.60)
    # The document's own figures for the same step, on the base it states.
    assert m.final_score(74.60, 2.00, -3.00) == pytest.approx(73.60)


def test_the_final_score_stays_inside_the_scoring_range():
    assert m.final_score(99.5, 2.0, 0.0) == 100.0
    assert m.final_score(1.0, 0.0, -3.0) == 0.0


# --- 8.2.5 / 13.7 Score Controls and Rating Safeguards -----------------------------------

def test_a_safeguard_caps_the_grade_whatever_the_score_says():
    cap, reasons = m.safeguard_cap(["critical_governance"])
    assert cap == "B" and "Critical Governance Safeguard" in reasons[0]
    assert m.capped_grade("A+", cap) == "B"
    assert m.capped_grade("B+", cap) == "B"
    # A grade already below the cap is untouched.
    assert m.capped_grade("C", cap) == "C"
    assert m.capped_grade("D", cap) == "D"


def test_a_severe_and_continuing_impact_caps_harder():
    cap, _reasons = m.safeguard_cap(["severe_continuing_environmental_social"])
    assert cap == "C" and m.capped_grade("A", cap) == "C"


def test_the_most_restrictive_safeguard_wins():
    cap, reasons = m.safeguard_cap(["critical_governance", "severe_continuing_environmental_social"])
    assert cap == "C" and len(reasons) == 2


def test_no_safeguard_leaves_the_grade_alone():
    cap, reasons = m.safeguard_cap([])
    assert cap is None and reasons == []
    assert m.capped_grade("A+", None) == "A+"
    assert m.safeguard_cap(["not a safeguard"]) == (None, [])
