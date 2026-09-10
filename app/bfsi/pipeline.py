# Port of the scoring half of bfsi-calculator/lib/openai.php (see docs/analysis/bfsi.md
# §4c): the criteria lists, the three prompts, bfsi_page_units, bfsi_avg_scores,
# bfsi_pool_keywords, bfsi_select_keywords, bfsi_modal_value and bfsi_analyze.
#
# Every prompt string below is VERBATIM from the PHP source, sprintf placeholders
# (%1$s ...) included, so that a diff against lib/openai.php stays trivial. Rendering
# goes through _sprintf() instead of Python formatting for the same reason.
#
# bfsi_extract_and_fingerprint lives in app/bfsi/extract.py with the rest of extraction.
import json as jsonlib
import logging
import re

from app.bfsi.openai_client import BfsiOpenAiFatal, get_client
from app.core.rounding import php_round

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring criteria — parity with the "All Other Sector" ESG calculator.
#
# These are the KPI lists the ESG calculator scores against, held there in
# MongoDB (esg_score_calculator.esg_prompts, one document per category) and
# reproduced here VERBATIM so both tools judge the same report against the same
# yardstick. Do not edit one side without the other, or the two products will
# return different scores for the same PDF.
#
# Each list = domain criteria + urban-development pillars + mapped UN SDGs.
# All 17 SDGs are covered exactly once across the three categories:
#   E: 7, 13, 14, 15    S: 1, 2, 3, 4, 5, 6, 8, 10, 11, 12, 16    G: 9, 17
# ---------------------------------------------------------------------------

CRITERIA_E = """\
1. Renewable energy usage initiatives.
2. Carbon footprint reduction goals.
3. Waste management policies.
4. Transparency in environmental disclosures.
5. Biodiversity conservation efforts.
6. Water conservation and management practices.
7. Pollution control measures.
8. Sustainable sourcing and supply chain practices.
9. Compliance with environmental regulations.
10. Any other relevant environmental factors.
11. Urbanisation and Economic Growth
12. Climate and Resilience
13. Sanitation Practises
14. Housing and Urban Infrastructure
15. Affordable and clean energy
16. Climate action
17. Life below the river
18. Life on land"""

CRITERIA_S = """\
1. Diversity, equity, and inclusion efforts.
2. Employee welfare and safety.
3. Community engagement initiatives.
4. Human rights policies and practices.
5. Data and Digital
6. Youth and Inclusion
7. Urban Livelihood
8. No poverty
9. Zero hunger
10. Good health and well - being
11. Quality education
12. Gender Equality
13. Access to Clean water and sanitation
14. Decent work and economic growth
15. Sustainable cities and communities
16. Reduced inequality
17. Responsible consumption and production
18. Peace, justice, and strong institution"""

CRITERIA_G = """\
1. Board diversity and ethical leadership.
2. Compliance with regulations.
3. Accountability and stakeholder involvement.
4. Anti-corruption and bribery policies.
5. Executive compensation practices.
6. Shareholder rights and activism.
7. Data privacy and security measures.
8. Risk management and internal controls.
9. Transparency in financial reporting.
10. Legal and regulatory compliance.
11. Business ethics and code of conduct.
12. Corporate social responsibility initiatives.
13. Whistleblower protection policies.
14. Conflicts of interest management.
15. Urban Governance and Municipal Finance
16. Industry, innovation, and infrastructure
17. Partnership for the goals."""

CRITERIA = {"E": CRITERIA_E, "S": CRITERIA_S, "G": CRITERIA_G}

# Adjectives, not nouns — the ESG calculator's prompts read "analyze ... for
# environmental/social/governance performance".
ADJECTIVES = {"E": "environmental", "S": "social", "G": "governance"}
CAT_NAMES = {"E": "Environment", "S": "Social", "G": "Governance"}

# Mirrors the ESG calculator's prompt structure exactly: criteria list, then the
# response contract, then the text. Sent as a single user message (no system
# message) because that is how the ESG calculator calls it — see
# esg_score_calculator-master/llm/gpt.py::generate_score.
# %1$s = adjective ("environmental"), %2$s = criteria list, %3$s = report text.
CATEGORY_PROMPT = """\
Analyze the following text for %1$s performance. Evaluate the text based on:
%2$s

Identify the positive and negative keywords that influenced the score.

Provide the response in JSON format with the following fields:
- "reason": A Detailed explanation of the score.
- "score": A number between 0 and 100.
- "positive_keywords": A list of keywords or phrases that contributed positively to the score.
- "negative_keywords": A list of keywords or phrases that reduced the score.
- "sector": The sector of the company (e.g., technology, healthcare, finance).
- "industry": The industry of the company (e.g., software, pharmaceuticals, banking).

Text:
%3$s"""

# Verbatim from the ESG calculator's select_keyword() (utils/helper.py) — including
# its typos and the skipped "4." in the numbering — so both tools rank keywords the
# same way. %1$s = category name, %2$s = the pooled candidate list.
KEYWORD_PROMPT = """\
you are AI Assitant, you have great expert in ESG, you can select the best keyword from the list of keywords
    Before selecting the top 5 keyword from the given list of keywords, you need to check the following:
    1. Understand the category of the keywords
    2. Understand the context of the keywords
    3. Understand the relevance of the keywords
    5. Understand the importance of the keywords
    6. STRICTLY SELECT THE TOP 5 KEYWORDS which will be more relevant to the category of the keywords.
    %1$s

    %2$s
    Provide the response in JSON format with the following fields:
    - "keywords": A list of keywords or phrase maximum 5."""

# System message of the qualitative pass (openai.php:444-447, one concatenated string).
QUAL_SYSTEM = (
    'You are an ESG credit-risk analyst. Using the report text below, respond in JSON: '
    '{"top_risks":[<5 short strings>], "top_improvements":[<5 short strings>], '
    '"climate_risk":"<2-3 sentences>", "governance_summary":"<2-3 sentences>", '
    '"key_metrics":{"employees":"","women_pct":"","attrition":"","complaints":"","csr":""}}'
)

_PLACEHOLDER = re.compile(r"%(\d+)\$s")


def _sprintf(template: str, *args: str) -> str:
    """PHP sprintf() for the positional %N$s placeholders these templates use."""
    return _PLACEHOLDER.sub(lambda m: args[int(m.group(1)) - 1], template)


# PHP's \s (no /u modifier) and trim() are byte-wise ASCII; Python's \s and str.strip()
# also match Unicode spaces, which would split words the PHP splits kept together and so
# change the exact prompt text sent to the model. Match PHP.
_PHP_WS = re.compile(r"[ \t\n\r\f\v]+")
_PHP_TRIM = " \t\n\r\0\x0b"

_NUMERIC = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _is_numeric(value) -> bool:
    """PHP is_numeric(): ints/floats yes, bools no, numeric strings yes."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return _NUMERIC.match(value.strip()) is not None
    return False


def _s(value) -> str:
    """PHP (string) cast, where null becomes ''."""
    return "" if value is None else value if isinstance(value, str) else str(value)


def _php_json_encode(value) -> str:
    """PHP json_encode() defaults: no whitespace, escaped slashes, \\uXXXX non-ASCII."""
    return jsonlib.dumps(value, separators=(",", ":")).replace("/", "\\/")


def page_units(pages: list, max_words: int = 1200) -> list[dict]:
    """One page = one scoring unit, matching the ESG calculator (helper.py submits each
    page separately). This replaces the old flat 2000-word chunking, which poured every
    page into one bucket and dropped page_no — so a score could never be traced back to
    a page, and a single unit could straddle three unrelated sections.

    A page longer than max_words is split into parts that all keep the same page_no, so
    one dense page cannot blow the context window.
    """
    units = []
    for p in pages:
        words = [w for w in _PHP_WS.split(_s(p.get("text")).strip(_PHP_TRIM)) if w != ""]
        if not words:
            continue
        for i in range(0, len(words), max_words):
            units.append({"page_no": p.get("page_no"), "text": " ".join(words[i:i + max_words])})
    return units


def avg_scores(results: list) -> float:
    scores = [r.get("score") for r in results]
    scores = [s for s in scores if _is_numeric(s)]
    if not scores:
        return 0.0
    # Clamp EACH score before averaging, not just the mean. Clamping only the mean
    # lets a single hallucinated value (e.g. 150) drag the average up while still
    # landing under 100 — passing the check but inflating the grade.
    scores = [max(0.0, min(100.0, float(s))) for s in scores]
    return php_round(sum(scores) / len(scores), 2)


def pool(results: list, field: str, limit: int = 5) -> list[str]:
    """Pool a list-valued field across chunk results, dedupe, cap at limit."""
    all_kw = []
    for r in results:
        value = r.get(field) or []
        if not isinstance(value, (list, tuple)):
            value = [value]           # PHP (array) cast of a scalar
        for k in value:
            k = _s(k).strip(_PHP_TRIM)
            if k != "":
                all_kw.append(k)
    return list(dict.fromkeys(all_kw))[:limit]     # array_unique keeps the first occurrence


def select_keywords(cat_name: str, candidates: list, limit: int = 5) -> list[str]:
    """Ask the model to rank the pooled keywords, as the ESG calculator does, instead of
    taking whichever five happened to come back first. Falls back to the raw list on
    failure — a ranking call is not worth failing an entire analysis over."""
    if len(candidates) <= limit:
        return candidates
    try:
        prompt = _sprintf(KEYWORD_PROMPT, cat_name, _php_json_encode(list(candidates)))
        response = get_client().json(prompt)
        picked = response.get("keywords") if isinstance(response, dict) else None
        if isinstance(picked, dict):
            picked = list(picked.values())
        if isinstance(picked, list) and picked:
            clean = [k for k in (_s(x).strip(_PHP_TRIM) for x in picked) if k != ""]
            if clean:
                return clean[:limit]
    except BfsiOpenAiFatal:
        raise
    except Exception as e:
        logger.error("bfsi keyword ranking failed, using unranked: %s", e)
    return candidates[:limit]


def modal(results: list, field: str) -> str:
    """Most frequently returned value for a scalar field across chunk results."""
    counts: dict[str, int] = {}
    for r in results:
        v = _s(r.get(field)).strip(_PHP_TRIM)
        if v == "":
            continue
        v = v.lower()
        v = v[:1].upper() + v[1:]      # ucfirst(strtolower($v))
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return ""
    # arsort() is stable in PHP 8, so ties keep first-seen order; so does max().
    return max(counts.items(), key=lambda kv: kv[1])[0]


def bfsi_analyze(submission: dict, pages: list) -> dict:
    """Score an already-extracted report. Extraction is the caller's job so it can check
    the text-level cache before spending anything on the API."""
    units = page_units(pages)
    scores, keywords, negative_keywords, reasons = {}, {}, {}, {}
    all_results = []
    for cat, adjective in ADJECTIVES.items():
        prompts = {i: _sprintf(CATEGORY_PROMPT, adjective, CRITERIA[cat], u["text"])
                   for i, u in enumerate(units)}
        # One request per page, all in flight together. A unit whose retries were all
        # exhausted comes back None and is simply left out of the average.
        results = []
        for i, r in get_client().batch(prompts).items():
            if not isinstance(r, dict):
                continue
            r["page_no"] = units[i]["page_no"]     # page travels with the score
            results.append(r)
        scores[cat] = avg_scores(results)
        # Positives are ranked by a follow-up call, as the ESG calculator does.
        # Negatives are not — matching it exactly; flip this to rank them too if wanted.
        keywords[cat] = select_keywords(CAT_NAMES[cat], pool(results, "positive_keywords", 40))
        negative_keywords[cat] = pool(results, "negative_keywords")
        # Per-page justifications: the audit trail for how the category score was
        # reached, each citable back to a page in the uploaded report.
        reasons[cat] = []
        for r in results:
            text = _s(r.get("reason")).strip(_PHP_TRIM)
            if text == "":
                continue
            reasons[cat].append({
                "page": int(r["page_no"]),
                "score": float(r["score"]) if _is_numeric(r.get("score")) else None,
                "reason": text,
            })
        all_results.extend(results)

    # qualitative pass on a condensed digest (first ~6000 words)
    joined = " ".join(p["text"] for p in pages if "text" in p)
    # No trim and no NO_EMPTY flag here, matching the PHP: leading whitespace yields an
    # empty first "word", which counts against the 6000.
    digest = " ".join(_PHP_WS.split(joined)[:6000])
    qual_prompt = ("Company: " + _s(submission.get("borrower_name"))
                   + " | Industry: " + _s(submission.get("industry"))
                   + " | Loan type: " + _s(submission.get("loan_type"))
                   + "\n\nReport text (condensed):\n" + digest)
    qual = get_client().json(qual_prompt, QUAL_SYSTEM)
    if not isinstance(qual, dict):
        qual = {}

    def q(key, default):
        value = qual.get(key)                      # PHP ?? only falls back on null
        return default if value is None else value

    return {
        "e_score": scores["E"], "s_score": scores["S"], "g_score": scores["G"],
        "keywords": keywords,
        "negative_keywords": negative_keywords,
        "reasons": reasons,
        # Sector/industry as detected from the report itself — the borrower also
        # self-selects these on the form, so a mismatch is worth a look.
        "detected_sector": modal(all_results, "sector"),
        "detected_industry": modal(all_results, "industry"),
        "top_risks": q("top_risks", []),
        "top_improvements": q("top_improvements", []),
        "climate_risk": q("climate_risk", ""),
        "governance_summary": q("governance_summary", ""),
        "key_metrics": q("key_metrics", []),
    }
