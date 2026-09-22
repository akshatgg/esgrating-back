# app/esg/scoring.py -- the ESG calculator's scoring, all in one place.
# Methodology: docs/ESG_SCORING_METHODOLOGY.md (user, 2026-09-18). ESG only; BFSI keeps
# its own scoring (app/core/kpis.py, app/bfsi/scoring.py).
#
#   0. page          -> the categories the page has content about (CLASSIFY_GUIDE); a page
#                       with no ESG content is not scored at all
#   1. KPI on a page -> for those categories only, the AI scores each KPI the page shows
#                       performance for, 0-100 on how GOOD it is (SCORE_GUIDE)
#   2. KPI           -> its best score on any page, capped at CAP_SCORE when any page
#                       showed poor performance; a KPI found nowhere scores 0
#   3. category      -> the average of ALL its KPI scores, missing ones included as 0
#                       (total of the scores / (number of KPIs x 100) x 100 is that average)
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
#
# The score says how GOOD the performance is, not how much detail is written (user,
# 2026-09-18: "u dont have to score on that basisi u have to score on the basiss of kpis
# delts is good or not whatever is written in the page" / "if only mention so still u have
# to give 0").
SCORE_GUIDE = (
    # The client's KPI evidence-validation brief (manager, 2026-09-22), kept whole. Two
    # sections are the pipeline's rather than his: the score bands (his 1-30 for a bare
    # mention would pay for naming a KPI, and his scale has no band for poor performance,
    # which capped_score() and contradiction() both read), and the response fields (the
    # pipeline scores a page against a numbered list, and the one-pager's KPI chips are
    # built from the keyword lists). Everything he requires per KPI -- evidence type,
    # direction, why the score -- is required in the reason line instead of as separate
    # JSON keys, so nothing is lost and the answer stays parseable.
    "You are an ESG KPI evidence validation and scoring analyst. Evaluate each numbered "
    "evaluation point ONLY against the text supplied below.\n"
    "\n"
    "You must NOT assume a point is evidenced merely because: the point's name or similar "
    "words appear in the text; a related ESG concept is mentioned; a general policy or "
    "commitment is mentioned; the company states an intention to act in future; the company "
    "describes an activity without demonstrating what the point requires; the text contains "
    "generic ESG language; or the text discusses the topic in a different context.\n"
    "\n"
    "For every point, first understand WHAT IT IS ACTUALLY MEASURING and what evidence is "
    "required to support a positive score. Then read the relevant passage in context and "
    "decide whether the disclosure actually satisfies that requirement.\n"
    "\n"
    "IMPORTANT: keyword matching is NEVER sufficient evidence. Semantic similarity is NEVER "
    "sufficient evidence. Understand the meaning, context, direction and sentiment of the "
    "disclosure before assigning a score. 'Today is a rainy day' is neither positive nor "
    "negative on its own; the meaning depends on what is being evaluated.\n"
    "\n"
    "If the point is 'Emissions reduction target', none of these automatically earn a "
    "positive score: 'The company shall establish emissions reduction targets.' / 'The "
    "company is committed to emissions reduction targets.' / 'The company intends to reduce "
    "emissions in the future.' They show a commitment or a policy. They do not show that "
    "emissions were reduced or that a measurable target with progress exists.\n"
    "\n"
    "Distinguish carefully between: (1) Mention -- the topic is merely mentioned; (2) Policy "
    "or Commitment -- a policy, intention, commitment or planned action; (3) Action or "
    "Programme -- an actual initiative, programme or action; (4) Measured Performance -- "
    "measured data, results, outcomes, targets, progress or quantified performance. Do not "
    "upgrade a point from one category to another without evidence. 'We aim to reduce GHG "
    "emissions by 30% by 2030' is a target, NOT evidence that emissions have already fallen "
    "30%. 'GHG emissions decreased from 100,000 tCO2e to 80,000 tCO2e' is measured "
    "reduction. The score must reflect what the disclosure actually demonstrates, not what "
    "the company intends to achieve.\n"
    "\n"
    "NEGATIVE / LIMITATION CONTEXT: identify whether the passage is negative, neutral or "
    "positive relative to what the point requires. 'Emissions increased by 12% during the "
    "reporting period despite our reduction programme' must NOT earn a positive emissions "
    "score merely because a reduction programme is mentioned. 'No progress was made against "
    "the emissions reduction target' must not score highly merely because a target exists. "
    "The requirement and the actual outcome must be weighed together.\n"
    "\n"
    "CONTEXT IS MANDATORY: read the complete relevant paragraph and, where needed, the "
    "surrounding paragraphs, tables, footnotes and headings before scoring. Do not score "
    "from an isolated sentence when the surrounding context changes its meaning.\n"
    "\n"
    "SOURCE GROUNDING: every point you score must be supported by what this text actually "
    "says, and your reason for it must give the supporting evidence, which of the four "
    "categories above it is, whether it is positive, neutral or negative, and why that "
    "earns the score. Do NOT manufacture evidence. Do NOT infer evidence from general ESG "
    "language. Do NOT use outside knowledge, other reports, other companies, websites or "
    "anything you know beyond this text.\n"
    "\n"
    "CRITICAL ANTI-HALLUCINATION RULE: if this text does not contain evidence supporting a "
    "point, that point must NOT be scored. If 'Palm oil sourcing policy' does not appear or "
    "is not substantively addressed, it must not be treated as evidenced simply because it "
    "exists in the KPI library. Likewise 'OECD Guidelines for MNEs' must not be scored "
    "unless the text discloses it. The KPI library defines WHAT TO LOOK FOR. The text "
    "determines WHETHER IT EXISTS.\n"
    "\n"
    "SCORING: do not give a high score merely because the text contains the point's "
    "keyword. The score must reflect the strength and specificity of the actual evidence, "
    "and how good the company's performance on that point is.\n"
    '- "kpi_scores": A list of [point number, score] pairs for the evaluation points listed '
    "above that this text genuinely evidences. Score each from 0 to 100:\n"
    "  0: only named, listed or mentioned, only promised, planned or pending, or too little "
    "to judge -- a point this text does not genuinely evidence scores 0\n"
    "  1-20: poor -- fines, penalties, lawsuits, incidents, accidents, a worsening trend, or "
    "an admitted failure\n"
    "  21-40: a policy, commitment or stated intention, with little evidence of "
    "implementation\n"
    "  41-60: a real action, programme or initiative in place, but no measured result\n"
    "  61-80: measured results, or real progress against a target\n"
    "  81-100: targets met, a measured improvement, independent assurance or certification\n"
    "These ranges are NOT automatic. A point requiring measured performance cannot reach "
    "61-100 because a policy exists. A point requiring a policy cannot score highly because "
    "the topic is mentioned. A negative outcome must not be treated as positive because the "
    "company has a policy or programme addressing it. Judge how good the performance is, "
    "never how much detail is written. Leave out the points this text says nothing about, "
    "and use an empty list if it says nothing about any of them.\n"
    '- "reason": One line for each point you scored, in the form '
    '"<point number> <score>: <evidence, its category, its direction, and why it earns that '
    'score>" -- for example "18 85: Scope 1 and 2 emissions down 22 percent against a 2030 '
    'target; measured performance, positive, a measured improvement against a stated '
    'target". Nothing else: no overall score and no overall verdict for the page.\n'
    '- "positive_keywords": The words or short phrases from this text that earned the higher '
    "scores above -- the measured results, targets met, certifications or assurance you saw. "
    "Quote the text, do not invent a phrase, and use an empty list if nothing earned a "
    "score.\n"
    '- "negative_keywords": The words or short phrases from this text that show poor '
    "performance -- the fines, penalties, lawsuits, incidents, accidents or worsening "
    "numbers behind any score of 1 to 20. Quote the text, and use an empty list if there are "
    "none.\n"
    "\n"
    "Never reward keyword presence alone. Never convert intention into achievement. Never "
    "convert policy into performance. Never convert activity into outcome. Never convert "
    "generic ESG language into point-specific evidence."
)

# The response fields SCORE_GUIDE replaces. The scoring prompt (esg_prompts for ESG,
# CATEGORY_PROMPT for BFSI) still asks for an overall page "score", for a "reason" that
# explains that score, and for keywords that "influenced the score" -- all left over from
# before KPI scoring. The page score is never used (a page's score is the average of its
# KPI scores), and a reason or a keyword list about it contradicts the KPI marks in the
# same row. So those lines are dropped from the prompt as it is sent, and SCORE_GUIDE
# defines reason and both keyword lists against the KPI scores instead. The stored
# templates are never modified.
_DROP_FIELDS = re.compile(r'(?m)^- "(?:score|reason|positive_keywords|negative_keywords)":.*\n?')

# Step 0: which categories the page is about, so only those categories' KPIs are matched
# against it (user, 2026-09-18: "Firstly, you have to analyse whether that thing lies in
# which category ... After that, you will have to match the KPIs"). A page can be in more
# than one category; a page with no ESG content is not scored at all.
CLASSIFY_GUIDE = (
    "Read the page below from a company report and say which ESG categories it has real "
    "content about.\n"
    "- Environment: emissions, energy, water, waste, pollution, biodiversity, climate, "
    "resource use, environmental compliance.\n"
    "- Social: employees, health and safety, training, diversity, human rights, labour, "
    "communities, customers, product safety and access.\n"
    "- Governance: board, directors, ethics, anti-corruption, audit, risk, shareholders, "
    "remuneration, transparency, regulatory compliance.\n"
    "Name a category only if the page says something about the company's own performance, "
    "practices or data in it. Leave every category out for a cover page, an index, a "
    "photograph caption, a purely financial table or anything else with no ESG content.\n"
    'Answer in JSON with one field:\n'
    '- "categories": a list holding any of "Environment", "Social", "Governance", or an '
    "empty list."
)


def classify_prompt(text: str) -> str:
    """The step 0 prompt for one page. Built by concatenation, not .format(), so page text
    containing braces or % signs is safe in both pipelines."""
    return CLASSIFY_GUIDE + "\n\nPage:\n" + text


def parse_categories(parsed) -> list[str]:
    """The categories from a step 0 answer, in CATEGORIES order. Junk and unknown names
    are dropped. None means the answer could not be read at all, so the caller falls back
    to scoring every category rather than silently skipping the page."""
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("categories")
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, (list, tuple)):
        return None
    named = {str(c).strip().lower() for c in raw if isinstance(c, (str, int, float))}
    return [c for c in CATEGORIES if c.lower() in named or PILLAR_CODES[c].lower() in named]


# Strengths and Gaps in the report: the score bands above, named.
STRONG_FROM = 61

# Poor performance anywhere caps the KPI (user, 2026-09-18). A fine on page 30 cannot be
# cancelled out by a good claim on page 4.
POOR_TO = 20
CAP_SCORE = 20.0

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
    "Text:"), and without the leftover "score" and "reason" fields it replaces
    (_DROP_FIELDS). Apply it to the template, before the page text is filled in."""
    i = prompt.rfind("\n\nText:")
    head, tail = (prompt, "") if i == -1 else (prompt[:i], prompt[i:])
    return _DROP_FIELDS.sub("", head).rstrip("\n") + "\n" + SCORE_GUIDE + (tail or "\n")


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


# The reason lines SCORE_GUIDE asks for: "<point number> <score>: <what the text shows>".
# The score is optional and a few separators are tolerated, because that is the part of the
# answer models are loosest about.
_REASON_LINE = re.compile(r"(?m)^\W*(\d+)\s*[ ,\-]?\s*(\d+(?:\.\d+)?)?\s*[:\-]\s*(\S.*?)\s*$")


def reason_text(parsed) -> str:
    """The answer's reason as one block of text. The scoring call returns its per-KPI
    lines either as a single string with one line each, or as a list of those lines --
    both say the same thing, and production answers with the list (2026-09-21). Anything
    else is no reason at all."""
    if not isinstance(parsed, dict):
        return ""
    value = parsed.get("reason")
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v).strip() for v in value if str(v).strip())
    return value.strip() if isinstance(value, str) else ""


def kpi_reasons(parsed, kpis: list[str]) -> dict[str, str]:
    """{KPI name: why it scored what it scored} from one page's answer. The scoring call
    writes one line per KPI it scored (SCORE_GUIDE), so the evidence for a score travels
    with it into the report, the CSV and the rating summary -- no extra call.

    An answer whose "reason" is one block of prose about the whole page gives {}: there is
    no way to tell which KPI it belongs to, so nothing is attributed to any of them."""
    text = reason_text(parsed)
    if not text:
        return {}
    out = {}
    for number, _score, line in _REASON_LINE.findall(text):
        names = _names([number], kpis)
        if names and names[0] not in out:
            out[names[0]] = line
    return out


def contradiction(parsed, scores: dict[str, float]) -> str:
    """"" or a note for a page whose own answer disagrees with itself: it lists negative
    keywords (fines, penalties, spills, incidents) yet scored none of its KPIs as poor
    performance (1-POOR_TO). Shown in the page-scores export as a row worth a human look;
    it never changes a score."""
    if not isinstance(parsed, dict) or not scores:
        return ""
    raw = parsed.get("negative_keywords")
    negatives = [str(k).strip() for k in raw if str(k).strip()] if isinstance(raw, list) else []
    if not negatives or min(scores.values()) <= POOR_TO:
        return ""
    return (f"Check: negative keywords ({', '.join(negatives[:5])}) but no KPI on this page "
            f"scored 1-{int(POOR_TO)}")


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


def capped_score(scores: list[float]) -> tuple[float, bool]:
    """A KPI's score from every page score it earned: its best, but held down to
    CAP_SCORE when any page showed poor performance (1-POOR_TO). Returns the score and
    whether the cap applied."""
    scores = [s for s in scores if s > 0]
    if not scores:
        return 0.0, False
    best = max(scores)
    if best > CAP_SCORE and min(scores) <= POOR_TO:
        return CAP_SCORE, True
    return best, False


def category_detail(pages: list, kpis: list[str]) -> dict:
    """A category's KPI Assessment: every KPI with its score, the pages it was found on and
    the evidence for that score, plus the category score.

    pages is [(page_no, {KPI name: 0-100})], or [(page_no, scores, {KPI name: reason})] to
    carry the reason the scoring call gave for each KPI (kpi_reasons). A KPI takes its best
    page score, capped when any page showed poor performance (capped_score); its evidence is
    the page that produced the score it ends up with -- the poor page when it is capped."""
    seen = {k: [] for k in kpis}
    found_on = {k: [] for k in kpis}
    for page in pages:
        page_no, scores = page[0], page[1]
        reasons = page[2] if len(page) > 2 and isinstance(page[2], dict) else {}
        for k, v in scores.items():
            if k not in seen:
                continue
            seen[k].append((v, page_no, reasons.get(k, "")))
            if page_no is not None and page_no not in found_on[k]:
                found_on[k].append(page_no)
    final = {}
    rows = []
    for k in kpis:
        score, capped = capped_score([v for v, _p, _r in seen[k]])
        final[k] = score
        rows.append(kpi_row(k, score, sorted(found_on[k], key=page_sort_key), capped,
                            _evidence(seen[k], capped)))
    return {"method": METHOD, "score": category_score(final), "kpis": rows}


# How many other scored pages a KPI keeps a reason for, beside the one that set its
# score. Enough to explain a score built from several pages without the stored report
# carrying a paragraph per KPI.
EVIDENCE_ALSO = 2


def _evidence(entries: list, capped: bool) -> dict:
    """Why a KPI scored what it scored. {page, reason, score} of the page the final score
    came from -- the worst page when it was capped, else the best -- plus `also`: the other
    pages that scored it, best first, so a score drawn from several pages can be explained
    by all of them and not just one (user, 2026-09-20). {} when the answer gave no reason."""
    scored = [e for e in entries if e[0] > 0]
    if not scored:
        return {}
    score, page, reason = min(scored) if capped else max(scored)
    if not reason:
        return {}
    ev = {"page": page, "reason": reason, "score": score}
    also = [e for e in sorted(scored, key=lambda e: e[0], reverse=True)
            if (e[0], e[1]) != (score, page) and e[2]]
    if also:
        ev["also"] = [{"page": p, "score": sc, "reason": r} for sc, p, r in also[:EVIDENCE_ALSO]]
    return ev


def kpi_row(kpi: str, score: float, pages: list, capped: bool = False, evidence: dict | None = None) -> dict:
    # "points" is the score again: older reports and the web read points.
    row = {"kpi": kpi, "score": score, "points": score, "level": level(score), "pages": pages}
    if capped:
        # Shown in the report and the CSV so a held-down score is never a surprise.
        row["capped"] = True
    if evidence:
        # Why this KPI scored what it scored, from the same call that scored it.
        row["evidence"] = evidence
    return row


def rescore_category(detail: dict, scores: dict[str, float]) -> dict:
    """The KPI Assessment with some KPI scores replaced (admin edits), and its score. The
    detail keeps its own method, so an older report keeps its older layout."""
    detail = dict(detail)
    # An analyst's own number replaces the AI's and is never capped again; an untouched
    # row keeps the cap it was scored with.
    detail["kpis"] = [kpi_row(r["kpi"], scores.get(r["kpi"], kpi_best(r)), r.get("pages") or [],
                              bool(r.get("capped")) and r["kpi"] not in scores,
                              None if r["kpi"] in scores else r.get("evidence"))
                      for r in detail.get("kpis") or []]
    detail["score"] = category_score({r["kpi"]: r["score"] for r in detail["kpis"]})
    return detail


def mark_pillar(detail: dict, pillar_score) -> None:
    """Record on a KPI Assessment the pillar score the report actually uses when an
    analyst set it by hand, so every report shows that number beside the KPI total
    instead of contradicting it. Cleared when the two agree again."""
    if not isinstance(detail, dict):
        return
    if isinstance(pillar_score, (int, float)) and abs(float(pillar_score) - float(detail.get("score") or 0)) > 0.005:
        detail["analyst_score"] = round(float(pillar_score), 2)
    else:
        detail.pop("analyst_score", None)


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


def kpi_summary_rows(coverage, totals: dict, weights_label: str, overall, grade: str) -> list[list]:
    """The rows under the page rows of a page-scores CSV: every KPI's best score and
    pages, each category's total, and the overall score with its weights and grade.
    Shared by the ESG and BFSI calculators. totals is {category name: score}."""
    if not isinstance(coverage, dict) or not coverage:
        return []
    rows = [[], SUMMARY_HEADER]
    for cat in CATEGORIES:
        detail = coverage.get(cat)
        if not isinstance(detail, dict):
            continue
        for r in detail.get("kpis") or []:
            pages = ", ".join(str(p) for p in r.get("pages") or []) or "-"
            if r.get("capped"):
                pages += f" (capped at {_num(CAP_SCORE)}: poor performance found)"
            rows.append([cat, r.get("kpi", ""), _num(kpi_best(r)), pages])
        total = float(totals.get(cat) or 0)
        # No note about who set the score: the export is a client document too.
        rows.append([f"{cat} total", "", _num(total), ""])
    rows.append(["Overall", weights_label, f"{float(overall or 0):.2f}", f"Grade {grade}"])
    return rows


def weights_label(weights: dict) -> str:
    """"35% Environment + 30% Social + 35% Governance" from fractions or percentages."""
    as_pct = all(v > 1 for v in weights.values())
    return " + ".join(f"{round(weights[c] if as_pct else weights[c] * 100)}% {c}" for c in CATEGORIES)


def summary_rows(final: dict | None) -> list[list]:
    """The ESG CSV summary for a result (final_report_data)."""
    final = final or {}
    totals = {c: final.get(f"{PREFIX[c]}_score", 0) for c in CATEGORIES}
    return kpi_summary_rows(final.get("kpi_coverage"), totals, weights_label(weights_for(final)),
                            final.get("composite_score"), final.get("composite_score_performance", ""))
