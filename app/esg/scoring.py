# app/esg/scoring.py -- the ESG calculator's scoring, all in one place.
# Methodology: docs/ESG_SCORING_METHODOLOGY.md (user, 2026-09-17). ESG only; BFSI keeps
# its own scoring (app/core/kpis.py, app/bfsi/scoring.py).
#
#   1. KPI on a page -> the AI scores every KPI the page addresses, 0-100 (SCORE_GUIDE)
#   2. KPI           -> its best score on any page; a KPI found nowhere scores 0
#   3. category      -> total of best scores / (number of KPIs x 100) x 100
#   4. overall       -> 35% Environment + 30% Social + 35% Governance
#   5. grade         -> evaluate_score
#
# A page's score (the average of the KPIs found on it) is shown in the report and the CSV
# to explain that page. It never feeds the category or overall score.
#
# The KPIs come from the esg_kpis collection (scripts/import_esg_kpis.py): each metric with
# its Sub Pillar and Sub Pillar 1, sent to the AI as context. Without that collection the
# numbered list in the esg_prompts prompt is used, as before.
import re

from app.core.db import get_db
from app.core.kpis import _names, kpi_strengths, page_sort_key

# Stored on every result scored this way. Results without it (older runs) keep the
# strong/partial KPI points and the old weights.
METHOD = "kpi_score"

CATEGORIES = ("Environment", "Social", "Governance")
PREFIX = {"Environment": "environmental", "Social": "social", "Governance": "governance"}

WEIGHTS = {"Environment": 0.35, "Social": 0.30, "Governance": 0.35}
LEGACY_WEIGHTS = {"Environment": 0.30, "Social": 0.30, "Governance": 0.40}

MAX_SCORE = 100.0

# Added to each category's scoring prompt, after its response fields. No braces: the
# prompt is .format()ted with the page text.
SCORE_GUIDE = (
    '- "kpi_scores": A list of [point number, score] pairs, one for each evaluation point '
    'listed above that this text addresses, each scored from 0 to 100:\n'
    '  1-30: only mentioned, with no detail\n'
    '  31-60: a policy or commitment is described\n'
    '  61-80: specific actions or programmes are described\n'
    '  81-100: measured data, or targets with progress against them\n'
    'Leave out the points this text does not address. Use an empty list if it addresses none.'
)

# Strengths and Gaps in the report: the score bands above, named.
STRONG_FROM = 61


# --- 0. The KPI list --------------------------------------------------------------------------

KPI_COLLECTION = "esg_kpis"
PILLAR_CODES = {"Environment": "E", "Social": "S", "Governance": "G"}

# The prompt's own numbered KPI list: consecutive "1. ..." lines.
_NUMBERED_LIST = re.compile(r"(?m)^[ \t]*\d+\.[ \t]+\S.*(?:\n[ \t]*\d+\.[ \t]+\S.*)*")


def load_kpis(category: str) -> list[dict]:
    """The category's metrics from esg_kpis in sheet order: [{metric, sub_pillar,
    sub_pillar_1}]. Meta rows (company facts, not scorable) are left out. Empty when the
    collection has no metrics for the category."""
    return list(get_db()[KPI_COLLECTION].find(
        {"pillar": PILLAR_CODES[category], "is_meta": {"$ne": True}},
        {"_id": 0, "metric": 1, "sub_pillar": 1, "sub_pillar_1": 1},
    ).sort("order", 1))


def kpi_list_text(kpis: list[dict]) -> str:
    """The metrics numbered 1..N, grouped under their Sub Pillar and Sub Pillar 1 so the AI
    knows what each short metric name is about. Only the numbered lines are KPIs
    (app/core/kpis.py parse_kpi_list skips the headings)."""
    lines, sub_pillar, sub_pillar_1 = [], None, None
    for i, k in enumerate(kpis, 1):
        if k["sub_pillar"] != sub_pillar:
            sub_pillar, sub_pillar_1 = k["sub_pillar"], None
            lines.append(f"Sub Pillar: {sub_pillar}")
        if k["sub_pillar_1"] != sub_pillar_1:
            sub_pillar_1 = k["sub_pillar_1"]
            lines.append(f"  Sub Pillar 1: {sub_pillar_1}")
        lines.append(f"    {i}. {k['metric']}")
    return "\n".join(lines)


def with_kpi_list(prompt: str, kpis: list[dict]) -> str:
    """The prompt with its numbered KPI list replaced by the esg_kpis metrics and their
    context. Unchanged when there are no metrics or the prompt has no numbered list."""
    if not kpis:
        return prompt
    block = kpi_list_text(kpis)
    return _NUMBERED_LIST.sub(lambda _m: block, prompt, count=1)


# --- 1. KPI scores on one page ---------------------------------------------------------------

def with_score_guide(prompt: str) -> str:
    """The category's scoring prompt with SCORE_GUIDE after its response fields (before
    "Text:"). Apply it to the template, before the page text is filled in."""
    i = prompt.rfind("\n\nText:")
    if i == -1:
        return prompt + "\n" + SCORE_GUIDE
    return prompt[:i] + "\n" + SCORE_GUIDE + prompt[i:]


def _score(value) -> float | None:
    try:
        n = float(str(value).strip())
    except ValueError:
        return None
    if n != n:  # NaN
        return None
    return max(0.0, min(MAX_SCORE, n))


def page_kpi_scores(parsed, kpis: list[str]) -> dict[str, float]:
    """{KPI name: 0-100} from one page's scoring answer.

    "kpi_scores" is read as [point number, score] pairs ({number: score} is accepted too).
    Unknown point numbers and junk are dropped, a KPI named twice keeps its higher score,
    and a 0 is left out (the page doesn't address that KPI). An answer that only has the
    older strong/partial lists reads as 100 / 50."""
    if not isinstance(parsed, dict):
        return {}
    raw = parsed.get("kpi_scores")
    if raw is None:
        return kpi_strengths(parsed, kpis)
    pairs = raw.items() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    out = {}
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        names = _names([pair[0]], kpis)
        score = _score(pair[1])
        if names and score:
            out[names[0]] = max(out.get(names[0], 0.0), score)
    return out


def page_score(scores: dict[str, float]) -> float:
    """A page's score: the average of the KPIs found on it; 0 when it has none."""
    if not scores:
        return 0.0
    return round(sum(scores.values()) / len(scores), 2)


def kpi_labels(scores: dict[str, float], kpis: list[str]) -> list[str]:
    """A page's KPIs for the report and the CSV: "name (80)", highest score first."""
    ranked = sorted((k for k in kpis if k in scores), key=lambda k: -scores[k])
    return [f"{k} ({_num(scores[k])})" for k in ranked]


def _num(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.2f}".rstrip("0").rstrip(".")


# --- 2 + 3. KPI best scores and the category score -------------------------------------------

def level(score: float) -> str:
    """Strong (61-100), partial (1-60) or none (0), for Strengths and Gaps."""
    return "strong" if score >= STRONG_FROM else "partial" if score > 0 else "none"


def category_score(best: dict[str, float]) -> float:
    """Total of the KPIs' best scores as a percentage of the maximum, rounded to 2."""
    if not best:
        return 0.0
    return round(sum(best.values()) / (len(best) * MAX_SCORE) * 100, 2)


def category_detail(pages: list, kpis: list[str]) -> dict:
    """A category's KPI Assessment: every KPI with its best score and the pages it was
    found on, and the category score. pages is [(page_no, {KPI name: 0-100})]."""
    best = {k: 0.0 for k in kpis}
    found_on = {k: [] for k in kpis}
    for page_no, scores in pages:
        for k, v in scores.items():
            if k not in best:
                continue
            best[k] = max(best[k], v)
            if page_no is not None and page_no not in found_on[k]:
                found_on[k].append(page_no)
    return {
        "method": METHOD,
        "score": category_score(best),
        "kpis": [kpi_row(k, best[k], sorted(found_on[k], key=page_sort_key)) for k in kpis],
    }


def kpi_row(kpi: str, score: float, pages: list) -> dict:
    # "points" is the score again: older reports and the web read points.
    return {"kpi": kpi, "score": score, "points": score, "level": level(score), "pages": pages}


def rescore_category(detail: dict, scores: dict[str, float]) -> dict:
    """The KPI Assessment with some KPI scores replaced (admin edits), and its score. The
    detail keeps its own method, so an older report keeps its older layout."""
    detail = dict(detail)
    detail["kpis"] = [kpi_row(r["kpi"], scores.get(r["kpi"], kpi_best(r)), r.get("pages") or [])
                      for r in detail.get("kpis") or []]
    detail["score"] = category_score({r["kpi"]: r["score"] for r in detail["kpis"]})
    return detail


def kpi_best(row: dict) -> float:
    """A KPI Assessment row's best score. Older reports stored points (100 / 50 / 0),
    which are on the same 0-100 scale."""
    for key in ("score", "points"):
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


# --- 4 + 5. Overall score and grade ----------------------------------------------------------

def weights_for(final: dict | None) -> dict:
    """The weights a result was scored with: WEIGHTS for this method, else the old ones."""
    return WEIGHTS if (final or {}).get("scoring_method") == METHOD else LEGACY_WEIGHTS


def composite_score(environmental, social, governance, weights: dict = WEIGHTS):
    """The overall score. Unrounded, as it is stored."""
    return (
        weights["Environment"] * environmental +
        weights["Social"] * social +
        weights["Governance"] * governance
    )


def evaluate_score(score):
    """(grade, label) for a score. The score is truncated first: 79.9 is 79 -> B+."""
    score = int(score)
    if score > 90:
        return "A+", "Outstanding"
    elif 80 <= score <= 90:
        return "A", "Excellent"
    elif 71 <= score <= 79:
        return "B+", "Very Good"
    elif 61 <= score <= 70:
        return "B", "Good"
    elif 40 <= score <= 60:
        return "C", "Average"
    else:
        return "D", "Below Average"


def grade_all(final: dict) -> None:
    """Set the grade and label of each category and of the overall score on a result."""
    for key in ("environmental", "social", "governance", "composite"):
        grade, label = evaluate_score(final[f"{key}_score"])
        final[f"{key}_score_performance"] = grade
        final[f"{key}_score_performance_label"] = label


# --- CSV summary -----------------------------------------------------------------------------

SUMMARY_HEADER = ["Category", "KPI", "Best Score", "Found on Pages"]


def summary_rows(final: dict | None) -> list[list]:
    """The rows under the page rows of the page-scores CSV: every KPI's best score and
    pages, each category's total and the overall score with its grade and weights."""
    coverage = (final or {}).get("kpi_coverage")
    if not isinstance(coverage, dict) or not coverage:
        return []
    rows = [[], SUMMARY_HEADER]
    for cat in CATEGORIES:
        detail = coverage.get(cat)
        if not isinstance(detail, dict):
            continue
        for r in detail.get("kpis") or []:
            pages = ", ".join(str(p) for p in r.get("pages") or []) or "-"
            rows.append([cat, r.get("kpi", ""), _num(kpi_best(r)), pages])
        rows.append([f"{cat} total", "", _num(float(final.get(f"{PREFIX[cat]}_score", 0) or 0)), ""])
    w = weights_for(final)
    weights = " + ".join(f"{round(w[c] * 100)}% {c}" for c in CATEGORIES)
    overall = float(final.get("composite_score", 0) or 0)
    rows.append(["Overall", weights, f"{overall:.2f}", f"Grade {final.get('composite_score_performance', '')}"])
    return rows
