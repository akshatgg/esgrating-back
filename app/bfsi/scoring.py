# Port of bfsi-calculator/lib/scoring.php + the grade/recommendation helpers from
# config/options.php (see docs/analysis/bfsi.md §4d). bfsi_compliance() and
# BFSI_ANSWER_POINTS are dead code in the PHP source (unused questionnaire step)
# and are intentionally not ported.
from app.bfsi.options import LOANTYPE_TO_WEIGHTAGE, WEIGHTAGE

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


def bfsi_grade(score: float) -> dict:
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


def bfsi_overall(e: float, s: float, g: float, loan_type: str) -> dict:
    row = LOANTYPE_TO_WEIGHTAGE.get(loan_type, "Working Capital")
    w = WEIGHTAGE[row]
    overall = round((w["e"] * e + w["s"] * s + w["g"] * g) / 100, 2)
    gr = bfsi_grade(overall)
    return {
        "overall": overall,
        "grade": gr["grade"],
        "label": gr["label"],
        "weights": w,
        "weightage_row": row,
    }
