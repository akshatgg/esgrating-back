# app/esg/scoring.py -- the ESG calculator's scoring, all in one place.
# Methodology: docs/ESG_SCORING_METHODOLOGY.md (user, 2026-09-18). ESG only; BFSI keeps
# its own scoring (app/core/kpis.py, app/bfsi/scoring.py).
#
#   0. page          -> what the page contains: pillars, themes, relevance (CLASSIFY_GUIDE).
#                       Reported only -- his sections 3 and 18 forbid classification
#                       removing a page from KPI analysis, so every page is scored
#   1. KPI on a page -> against ALL THREE pillars, the AI returns one finding per KPI the
#                       page evidences, with a 0-100 score_contribution (SCORE_GUIDE s.30)
#   2. KPI           -> the strongest contribution from any page; a KPI found nowhere
#                       scores 0. No cap: his sections 5 and 26 forbid weak evidence on one
#                       page overriding stronger evidence on another
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
from app.esg import methodology, prompts

# Stored on every result scored this way. Results without it (older runs) keep the
# strong/partial KPI points and the old weights.
METHOD = "kpi_score"

CATEGORIES = ("Environment", "Social", "Governance")
PREFIX = {"Environment": "environmental", "Social": "social", "Governance": "governance"}

WEIGHTS = {"Environment": 0.35, "Social": 0.30, "Governance": 0.35}
LEGACY_WEIGHTS = {"Environment": 0.30, "Social": 0.30, "Governance": 0.40}

MAX_SCORE = 100.0

# Metric names are matched on meaning, not layout: the model echoes the KPI back and may
# re-space or re-case it.
_WS = re.compile(r"\s+")

# The CFC KPI Evidence Analysis and Scoring Engine prompt, adopted verbatim from the
# client's own document (CFC, 2026-09-23; app/esg/prompts/score_guide.txt). It replaces the
# prompt written here before it, on the instruction to follow his prompts as issued.
#
# His section 30 defines the shape of ONE KPI finding. It does not say how a page returns
# many of them, and the report still needs the page-level keyword lists that build the
# one-pager's KPI chips and the CSV export. RESPONSE_ENVELOPE adds only that wrapper --
# a list of his objects plus the two keyword lists. None of his wording is altered.
SCORE_GUIDE = prompts.load("score_guide") + """

========================================================
RESPONSE FORMAT
========================================================
Return JSON with exactly these fields:
- "kpi_findings": a list holding one object, in the form given in section 30 above, for
  every KPI in the library above for which THIS page contains relevant evidence. Use the
  KPI's exact "metric" text as listed. Leave out the KPIs this page says nothing about,
  and use an empty list when the page evidences none of them.
- "positive_keywords": the words or short phrases from this page that carried the higher
  score contributions -- the measured results, targets met, certifications or assurance you
  saw. Quote the page; do not invent a phrase. Empty list if there are none.
- "negative_keywords": the words or short phrases from this page that show poor
  performance -- the fines, penalties, lawsuits, incidents, accidents, missed targets or
  worsening numbers you saw. Quote the page. Empty list if there are none."""

_DROP_FIELDS = re.compile(r'(?m)^- "(?:score|reason|positive_keywords|negative_keywords)":.*\n?')

# The CFC Page Classification and Relevance Engine prompt, adopted verbatim from the
# client's own document (CFC, 2026-09-23; app/esg/prompts/classify_guide.txt).
#
# His sections 3 and 18 are explicit that classification must never remove a page from KPI
# scoring -- "Every page remains available for KPI evidence evaluation" -- so the pipeline
# no longer uses this answer to choose which pillars to score. It is read for the pillars,
# themes and relevance it reports, which the narrative uses; every page is scored against
# all three pillars regardless of what comes back here.
CLASSIFY_GUIDE = prompts.load("classify_guide")


def classify_prompt(text: str) -> str:
    """The step 0 prompt for one page. Built by concatenation, not .format(), so page text
    containing braces or % signs is safe in both pipelines."""
    return CLASSIFY_GUIDE + "\n\nPage:\n" + text


def parse_categories(parsed) -> list[str]:
    """The pillars a classification answer reports, in CATEGORIES order.

    CLASSIFY_GUIDE section 17 returns "primary_pillars" and "secondary_pillars" as the
    codes E, S and G; both are read, because his section 11 allows a page to be primarily
    one pillar and substantively another. The older "categories" field is still accepted so
    results stored before this prompt keep reading.

    This is reported, not used to choose what to score: his sections 3 and 18 forbid
    classification removing a page from KPI analysis, so the pipeline scores every page
    against all three pillars whatever comes back here."""
    if not isinstance(parsed, dict):
        return None
    raw, readable = [], False
    for field in ("primary_pillars", "secondary_pillars", "categories"):
        value = parsed.get(field)
        if isinstance(value, dict):
            value = list(value.values())
        if isinstance(value, (list, tuple)):
            raw.extend(value)
            readable = True          # an empty list is an answer: no pillars on this page
        elif isinstance(value, str):
            raw.append(value)
            readable = True
    if not readable:
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
    """The category's metrics from esg_kpis in sheet order: [{metric, question, sub_pillar,
    sub_pillar_1}]. Meta rows (company facts, not scorable) are left out. Empty when the
    collection has no metrics for the category."""
    return list(get_db()[KPI_COLLECTION].find(
        {"pillar": PILLAR_CODES[category], "is_meta": {"$ne": True}},
        {"_id": 0, "metric": 1, "question": 1, "sub_pillar": 1, "sub_pillar_1": 1},
    ).sort("order", 1))


def kpi_list_text(kpis: list[dict]) -> str:
    """The metrics numbered 1..N, grouped under their Sub Pillar and Sub Pillar 1, each with
    the Question it asks.

    SCORE_GUIDE section 2 makes the Question the primary test -- "The Metric is only the KPI
    label" -- so it is sent with every KPI. It goes on its own unnumbered line: only the
    numbered lines are KPIs (app/core/kpis.py parse_kpi_list skips everything else), and the
    pipeline reads the KPI names back out of the rendered prompt, so a Question appended to
    the numbered line would rename every KPI in the report."""
    lines, sub_pillar, sub_pillar_1 = [], None, None
    for i, k in enumerate(kpis, 1):
        if k["sub_pillar"] != sub_pillar:
            sub_pillar, sub_pillar_1 = k["sub_pillar"], None
            lines.append(f"Sub Pillar: {sub_pillar}")
        if k["sub_pillar_1"] != sub_pillar_1:
            sub_pillar_1 = k["sub_pillar_1"]
            lines.append(f"  Sub Pillar 1: {sub_pillar_1}")
        lines.append(f"    {i}. {k['metric']}")
        question = str(k.get("question", "")).strip()
        if question:
            lines.append(f"       Question: {question}")
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
    (_DROP_FIELDS). Apply it to the template, before the page text is filled in.

    Never read the KPI names back out of the result. SCORE_GUIDE numbers its own 32
    sections, so app/core/kpis.py parse_kpi_list would take "1. CORE PRINCIPLE" and 40
    others for KPIs. Parse scoring_prompt() instead, which is this prompt without the
    guide -- that is what app/esg/pipeline.py does."""
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


def _findings(parsed) -> list[dict]:
    """The page's KPI finding objects (SCORE_GUIDE section 30), or [] when the answer has
    none. Only objects are kept, so a stray string in the list cannot break a page."""
    raw = parsed.get("kpi_findings") if isinstance(parsed, dict) else None
    return [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []


def _finding_kpi(finding: dict, kpis: list[str]) -> str | None:
    """The KPI a finding is about: its "metric" matched against the library sent to the
    model, ignoring case and spacing. Falls back to the point number for answers written
    before the metric was echoed back."""
    metric = str(finding.get("metric", "")).strip()
    if metric:
        wanted = _WS.sub(" ", metric).casefold()
        for kpi in kpis:
            if _WS.sub(" ", kpi).casefold() == wanted:
                return kpi
    names = _names([finding.get("page_kpi_number", finding.get("number"))], kpis)
    return names[0] if names else None


def page_kpi_scores(parsed, kpis: list[str]) -> dict[str, float]:
    """{KPI name: 0-100} from one page's scoring answer.

    The answer is SCORE_GUIDE section 30: one object per KPI this page evidences, carrying
    the KPI's own "metric" and its "score_contribution" for this page. A KPI named twice
    keeps its higher contribution, and a 0 is left out -- his section 4 is explicit that a
    page with no evidence for a KPI is not a negative result for it, so it must not be
    recorded as a score of 0 on that page.

    Results stored before this prompt are still read: "kpi_scores" as [point number, score]
    pairs, and older runs still through their strong/partial lists."""
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for finding in _findings(parsed):
        # "evidence_found_on_page": false is his way of saying the page has nothing for the
        # KPI, whatever number accompanies it.
        if finding.get("evidence_found_on_page") is False:
            continue
        kpi = _finding_kpi(finding, kpis)
        score = _score(finding.get("score_contribution"))
        if kpi and score:
            out[kpi] = max(out.get(kpi, 0.0), score)
    if out:
        return out

    raw = parsed.get("kpi_scores")
    if raw is None:
        return kpi_strengths(parsed, kpis)
    pairs = raw.items() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
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
    out = {}
    for finding in _findings(parsed):
        kpi = _finding_kpi(finding, kpis)
        reason = str(finding.get("score_reason", "")).strip()
        if kpi and reason and kpi not in out:
            out[kpi] = reason
    if out:
        return out

    text = reason_text(parsed)
    if not text:
        return {}
    out = {}
    for number, _score, line in _REASON_LINE.findall(text):
        names = _names([number], kpis)
        if names and names[0] not in out:
            out[names[0]] = line
    return out


def kpi_evidence_types(parsed, kpis: list[str]) -> dict[str, str]:
    """{KPI name: evidence_type} from one page's answer (SCORE_GUIDE section 30).

    This is what the methodology's 8.3 indicator weighting is applied to -- whether the
    evidence was a policy, an implementation, a target, measured performance or an assured
    outcome. {} for an answer that does not report it, which leaves every indicator at the
    default weight rather than guessing."""
    out = {}
    for finding in _findings(parsed):
        kpi = _finding_kpi(finding, kpis)
        kind = str(finding.get("evidence_type", "")).strip()
        if kpi and kind and kpi not in out:
            out[kpi] = kind
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
    """A KPI's score from every page contribution it earned: the strongest.

    The second value is whether a cap was applied, and is now always False. Until
    2026-09-23 a KPI whose evidence was poor on any page (1-POOR_TO) was held down to
    CAP_SCORE. SCORE_GUIDE sections 5 and 26 rule that out -- "weak evidence from one page
    must not override stronger relevant evidence elsewhere", and the final score is "the
    strongest reliable, relevant and applicable evidence available across the COMPLETE
    source document" -- so the cap is not applied to anything scored under his prompt.

    The flag, CAP_SCORE and POOR_TO stay because reports scored before this still carry
    "capped": True on their rows and must keep rendering the way they were issued."""
    scores = [s for s in scores if s > 0]
    return (max(scores), False) if scores else (0.0, False)


def category_detail(pages: list, kpis: list[str], meta: dict | None = None,
                    sector: str = "") -> dict:
    """A category's KPI Assessment: every KPI with its score, the pages it was found on and
    the evidence for that score, plus the category score.

    pages is [(page_no, {KPI name: 0-100})], or with a third element {KPI name: reason} and
    a fourth {KPI name: evidence_type}. A KPI takes its strongest page contribution
    (capped_score); its evidence is the page that produced it.

    meta is {KPI name: {theme, key_issue}} from esg_kpis. With it the category score is the
    methodology's own: indicators weighted by 8.3 evidence level and 7.2 materiality, then
    Key Issue -> Theme -> Pillar (8.2.7 steps 2-5). Without it -- BFSI, and any run whose
    KPIs are not in the library -- the score stays the flat average it has always been, so
    nothing that already works changes shape."""
    seen = {k: [] for k in kpis}
    found_on = {k: [] for k in kpis}
    kinds: dict[str, str] = {}
    for page in pages:
        page_no, scores = page[0], page[1]
        reasons = page[2] if len(page) > 2 and isinstance(page[2], dict) else {}
        types = page[3] if len(page) > 3 and isinstance(page[3], dict) else {}
        for k, v in scores.items():
            if k not in seen:
                continue
            seen[k].append((v, page_no, reasons.get(k, "")))
            # The kind of evidence that set the score: the strongest page wins, as it does
            # for the score itself.
            if types.get(k) and v >= max((s for s, _p, _r in seen[k]), default=0):
                kinds[k] = types[k]
            if page_no is not None and page_no not in found_on[k]:
                found_on[k].append(page_no)
    final = {}
    rows = []
    for k in kpis:
        score, capped = capped_score([v for v, _p, _r in seen[k]])
        final[k] = score
        row = kpi_row(k, score, sorted(found_on[k], key=page_sort_key), capped,
                      _evidence(seen[k], capped))
        info = (meta or {}).get(k) or {}
        if info:
            row["theme"] = info.get("theme", "")
            row["key_issue"] = info.get("key_issue", "")
            row["evidence_type"] = kinds.get(k, "")
            row["materiality"] = methodology.materiality_of(row["theme"], sector, "")
        rows.append(row)

    detail = {"method": METHOD, "score": category_score(final), "kpis": rows}
    if meta:
        weighted = methodology.aggregate([
            {"score": r["score"], "theme": r["theme"], "key_issue": r["key_issue"],
             "evidence_type": r.get("evidence_type"), "materiality": r["materiality"]}
            for r in rows if "materiality" in r])
        # None means every indicator was Not Applicable; the flat average stands rather
        # than a pillar disappearing from the rating.
        if weighted["score"] is not None:
            detail["score"] = weighted["score"]
            detail["flat_score"] = category_score(final)
        detail["weighting"] = {"themes": weighted["themes"], "applicable": weighted["applicable"],
                               "excluded": weighted["excluded"],
                               "sector": (methodology.sector_profile(sector) or ("", ()))[0]}
    return detail


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
    """The weights this report is scored with: the ones an analyst set on it, else WEIGHTS
    for this method, else the old ones. Every reader of the weights comes through here --
    the overall score, the report table, the CSV, the Word summary and the writer -- so a
    weight changed in the report changes all of them (user, 2026-09-22)."""
    stored = (final or {}).get("weights")
    if isinstance(stored, dict) and all(
        isinstance(stored.get(c), (int, float)) and not isinstance(stored.get(c), bool)
        for c in CATEGORIES
    ):
        # Stored as the marks the report shows (35), used here as the fraction the
        # overall score is built from (0.35).
        return {c: float(stored[c]) / 100 for c in CATEGORIES}
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
