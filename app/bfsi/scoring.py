# app/bfsi/scoring.py -- the BFSI calculator's scoring. Methodology:
# docs/BFSI_SCORING_METHODOLOGY.md (user, 2026-09-17).
#
# E, S and G are scored exactly like the ESG calculator (app/esg/scoring.py): each KPI a
# page addresses is scored 0-100, every KPI keeps its best score (not found = 0), and a
# category is the total of best scores as a percentage of the maximum. Only the overall
# score differs: it uses the loan type's weights (WEIGHTAGE), then the grade and the
# lending recommendation.
#
# Grade/recommendation helpers were ported from bfsi-calculator/config/options.php (see
# docs/analysis/bfsi.md §4d). bfsi_compliance() and BFSI_ANSWER_POINTS are dead code in
# the PHP source (unused questionnaire step) and are intentionally not ported.
from app.bfsi.options import LOANTYPE_TO_WEIGHTAGE, WEIGHTAGE
from app.core.rounding import php_round
from app.esg import scoring as kpi_scoring

# Marks an ai_analysis scored KPI by KPI (0-100). Older results keep strong/partial KPI
# points and their original grades.
METHOD = kpi_scoring.METHOD

CAT_KEYS = {"Environment": "e", "Social": "s", "Governance": "g"}

# Grade -> label, in ladder order. Used for the ladder itself and for labelling
# the per-category (E/S/G) grade column in reports.
GRADE_LABELS: dict[str, str] = {
    "A+": "Outstanding",
    "A": "Excellent",
    "B+": "Very Good",
    "B": "Good",
    "C": "Average",
    "D": "Below Average",
}


def is_kpi_scored(ai: dict | None) -> bool:
    return (ai or {}).get("scoring_method") == METHOD


def bfsi_grade(score: float, whole: bool = False) -> dict:
    """Grade for a score. whole=True (KPI-scored reports) drops the decimals first, as
    the ESG calculator does, so both give the same grade (90.5 -> 90 -> A)."""
    if whole:
        score = int(score)
    if score > 90:
        grade = "A+"
    elif score >= 80:
        grade = "A"
    elif score >= 71:
        grade = "B+"
    elif score >= 61:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"
    return {"grade": grade, "label": GRADE_LABELS[grade]}


def bfsi_recommendation(grade: str) -> str:
    if grade in ("A+", "A"):
        return "Favorable — low ESG credit risk"
    if grade in ("B+", "B"):
        return "Moderate — lend with standard ESG conditions"
    if grade == "C":
        return "Caution — enhanced ESG due diligence recommended"
    return "High risk — detailed review before lending"


def bfsi_overall(e: float, s: float, g: float, loan_type: str, whole_grade: bool = False) -> dict:
    """The overall score with the loan type's weights, its grade (whole_grade: see
    bfsi_grade) and the weights used."""
    row = LOANTYPE_TO_WEIGHTAGE.get(loan_type, "Working Capital")
    w = WEIGHTAGE[row]
    overall = php_round((w["e"] * e + w["s"] * s + w["g"] * g) / 100, 2)
    gr = bfsi_grade(overall, whole_grade)
    return {
        "overall": overall,
        "grade": gr["grade"],
        "label": gr["label"],
        "weights": w,
        "weightage_row": row,
    }


def summary_rows(ai: dict | None, e: float, s: float, g: float, loan_type: str) -> list[list]:
    """The BFSI page-scores CSV summary: every KPI's best score and pages, the category
    totals, and the overall score with the loan type's weights and grade."""
    ai = ai or {}
    ov = bfsi_overall(e, s, g, loan_type, is_kpi_scored(ai))
    weights = {cat: ov["weights"][key] for cat, key in CAT_KEYS.items()}
    label = f"{ov['weightage_row']}: {kpi_scoring.weights_label(weights)}"
    totals = {"Environment": e, "Social": s, "Governance": g}
    return kpi_scoring.kpi_summary_rows(ai.get("kpi_coverage"), totals, label, ov["overall"], ov["grade"])
