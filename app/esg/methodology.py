"""The CFC ESG Rating Methodology v1.0 (August 2026) scoring chain.

Section numbers below are that document's. Its calculation sequence (8.2.7) is:

    Evidence -> Indicator Score -> Weighted Indicator -> Key Issue -> Theme
    -> E/S/G Pillar Scores -> Base ESG Score -> Transition Adjustment
    -> Controversy Adjustment -> Rating Safeguards -> Final ESG Score -> Rating Grade

app/esg/scoring.py produces the indicator scores (step 1) from the page findings. This
module is every step after that. It holds no I/O and makes no AI calls: it is the
arithmetic, so it can be checked against the worked examples the document gives.

One deliberate reading, because the document is ambiguous and the two possibilities give
different scores. Annexure A assigns materiality per PILLAR per sector (Banks =
Moderate/High/Signature), while 7.2's multipliers are written for INDICATORS. Applying a
pillar's multiplier to every indicator inside it cancels out -- a constant factor does not
change a weighted mean -- so it could only take effect by re-weighting the pillars
themselves, which would weight them twice, since step 6 already fixes them at 35/30/35.
So materiality is applied where it actually varies: an indicator whose Theme is one of the
sector's Signature topics is Signature (2.00x), and the rest are Moderate (1.00x). The
pillar priorities are still reported (sector_profile) for the rating rationale. This is
flagged for the methodology owner; PILLAR_MATERIALITY_APPLIES switches it.
"""
from __future__ import annotations

import re

# --- 8.3 Indicator Weighting -----------------------------------------------------------
# What KIND of proof the evidence is. The model returns evidence_type per finding
# (SCORE_GUIDE section 30); these are his values mapped onto the document's six levels.
EVIDENCE_WEIGHTS = {
    "compliance": 0.75,          # Compliance / Basic Requirement
    "supporting evidence": 0.75,
    "insufficient evidence": 0.75,
    "policy strategy": 1.00,     # Policy / Strategy
    "policy": 1.00,
    "strategy": 1.00,
    "implementation": 1.20,      # Implementation / Management Process
    "management process": 1.20,
    "commitment": 1.25,          # Target / Commitment
    "target": 1.25,
    "measured performance": 1.50,  # Measured Performance / Outcome
    "negative performance": 1.50,  # measured too -- the outcome is simply adverse
    "outcome": 1.50,
    "verified outcome": 1.60,    # Assured / Independently Verified Performance
    "assured": 1.60,
}
DEFAULT_EVIDENCE_WEIGHT = 1.00   # an unrecognised type is treated as a policy

# --- 7.2 CFC Materiality Levels ---------------------------------------------------------
MATERIALITY_WEIGHTS = {
    "signature": 2.00,
    "high": 1.50,
    "moderate": 1.00,
    "low": 0.50,
    "not applicable": 0.00,      # excluded from scoring, not scored as zero
}
DEFAULT_MATERIALITY = "moderate"

# Whether Annexure A's pillar priorities also re-weight the pillars. False keeps the
# document's own step 6 weights (35/30/35) as the only pillar weighting. See the note above.
PILLAR_MATERIALITY_APPLIES = False

# --- Annexure A. Sector Materiality Matrix ----------------------------------------------
# The CSE 20 industry groups: (E priority, S priority, G priority, Signature topics).
SECTOR_MATERIALITY = {
    "Automobiles & Components": ("high", "high", "high",
                                 ("product emissions", "ohs", "product safety", "supplier traceability")),
    "Banks": ("moderate", "high", "signature",
              ("financed emissions", "inclusion", "customer protection", "aml", "cyber")),
    "Capital Goods": ("high", "high", "high",
                      ("energy", "materials", "contractor safety", "project impacts")),
    "Commercial & Professional Services": ("low", "high", "signature",
                                           ("talent", "ethics", "client confidentiality", "cyber")),
    "Consumer Durables & Apparel": ("high", "signature", "high",
                                    ("water", "chemicals", "labour", "supplier due diligence")),
    "Consumer Services": ("high", "high", "high",
                          ("water", "waste", "labour", "customer safety", "guest safety", "biodiversity")),
    "Diversified Financials": ("moderate", "high", "signature",
                               ("responsible credit", "affordability", "financed risk", "aml")),
    "Energy": ("signature", "high", "high",
               ("ghg", "spills", "air", "transition", "safety")),
    "Food & Staples Retailing": ("high", "high", "high",
                                 ("food safety", "cold-chain energy", "waste", "supply chain")),
    "Food, Beverage & Tobacco": ("signature", "high", "high",
                                 ("water", "agriculture", "packaging", "product responsibility")),
    "Health Care Equipment & Services": ("moderate", "signature", "signature",
                                         ("patient safety", "clinical waste", "data privacy", "access")),
    "Household & Personal Products": ("high", "high", "high",
                                      ("chemicals", "packaging", "product safety", "sourcing")),
    "Insurance": ("moderate", "signature", "signature",
                  ("catastrophe risk", "claims fairness", "investment risk", "data")),
    "Materials": ("signature", "signature", "high",
                  ("emissions", "water", "pollution", "ohs", "land")),
    "Pharma / Biotech / Life Sciences": ("high", "signature", "signature",
                                         ("product safety", "clinical ethics", "waste", "integrity")),
    "Real Estate": ("high", "high", "high",
                    ("embodied carbon", "water", "construction safety", "resilience")),
    "Retailing": ("moderate", "high", "high",
                  ("supply-chain labour", "customer protection", "packaging", "data")),
    "Telecommunication Services": ("high", "signature", "signature",
                                   ("energy", "e-waste", "digital inclusion", "cyber")),
    "Transportation": ("signature", "signature", "high",
                       ("fuel", "emissions", "safety", "resilience", "spills", "community")),
    "Utilities": ("signature", "high", "signature",
                  ("energy transition", "reliability", "affordability", "environment")),
}

PILLAR_INDEX = {"Environment": 0, "Social": 1, "Governance": 2}
_WORD = re.compile(r"[^a-z0-9]+")


def _key(text: str) -> str:
    return _WORD.sub(" ", str(text or "").casefold()).strip()


# What companies call themselves, mapped to the matrix. The pipeline takes the sector from
# the AI in the company's own words ("Commercial Banking", "Cement", "Oil and Gas"), which
# are not the CSE industry group names.
SECTOR_ALIASES = {
    "bank": "Banks", "banking": "Banks", "finance": "Diversified Financials",
    "financial": "Diversified Financials", "nbfc": "Diversified Financials",
    "insurance": "Insurance", "cement": "Materials", "steel": "Materials",
    "mining": "Materials", "chemical": "Materials", "chemicals": "Materials",
    "metals": "Materials", "paper": "Materials", "oil": "Energy", "gas": "Energy",
    "petroleum": "Energy", "coal": "Energy", "power": "Utilities",
    "electricity": "Utilities", "renewable": "Utilities", "water": "Utilities",
    "pharmaceutical": "Pharma / Biotech / Life Sciences",
    "pharmaceuticals": "Pharma / Biotech / Life Sciences",
    "pharma": "Pharma / Biotech / Life Sciences",
    "biotech": "Pharma / Biotech / Life Sciences",
    "hospital": "Health Care Equipment & Services",
    "healthcare": "Health Care Equipment & Services",
    "telecom": "Telecommunication Services", "telecommunications": "Telecommunication Services",
    "software": "Commercial & Professional Services", "technology": "Commercial & Professional Services",
    "it": "Commercial & Professional Services", "consulting": "Commercial & Professional Services",
    "logistics": "Transportation", "shipping": "Transportation", "airline": "Transportation",
    "aviation": "Transportation", "railways": "Transportation",
    "automobile": "Automobiles & Components", "automotive": "Automobiles & Components",
    "construction": "Capital Goods", "engineering": "Capital Goods",
    "manufacturing": "Capital Goods", "machinery": "Capital Goods",
    "property": "Real Estate", "realty": "Real Estate",
    "retail": "Retailing", "ecommerce": "Retailing",
    "apparel": "Consumer Durables & Apparel", "textile": "Consumer Durables & Apparel",
    "textiles": "Consumer Durables & Apparel", "hotel": "Consumer Services",
    "hospitality": "Consumer Services", "tourism": "Consumer Services",
    "food": "Food, Beverage & Tobacco", "beverage": "Food, Beverage & Tobacco",
    "agriculture": "Food, Beverage & Tobacco", "tea": "Food, Beverage & Tobacco",
    "plantation": "Food, Beverage & Tobacco", "tobacco": "Food, Beverage & Tobacco",
}

_STOPWORDS = {"and", "the", "of", "services", "products", "sector", "industry", "group", "companies"}


def _words(text: str) -> set[str]:
    return {w for w in _key(text).split() if len(w) > 2 and w not in _STOPWORDS}


def _shares_stem(a: str, b: str) -> bool:
    """Whether two words are the same word: "banking"/"banks", "chemical"/"chemicals"."""
    n = min(len(a), len(b))
    if n < 4:
        return a == b
    return a[:4] == b[:4] and (a.startswith(b) or b.startswith(a) or a[:5] == b[:5])


def sector_profile(sector: str) -> tuple[str, tuple] | None:
    """(matrix sector name, its row) for the sector the report reported, or None.

    Matched in the order exact, alias, then word stem, because the AI reports the sector in
    the company's own words. None when nothing matches, which leaves every indicator at the
    default materiality -- an unrecognised sector never silently reweights a rating."""
    wanted = _key(sector)
    if not wanted:
        return None
    for name, row in SECTOR_MATERIALITY.items():
        if _key(name) == wanted:
            return name, row
    for word in _words(sector):
        for alias, name in SECTOR_ALIASES.items():
            if _shares_stem(word, alias):
                return name, SECTOR_MATERIALITY[name]
    for name, row in SECTOR_MATERIALITY.items():
        if any(_shares_stem(w, k) for w in _words(sector) for k in _words(name)):
            return name, row
    return None


def materiality_of(theme: str, sector: str, category: str) -> str:
    """The materiality level of an indicator, from its Theme and the company's sector.

    An indicator whose Theme is one of the sector's Signature topics (Annexure A) is
    Signature; everything else applicable is Moderate. A sector not in the matrix leaves
    every indicator Moderate, so an unknown sector never silently reweights a rating."""
    found = sector_profile(sector)
    if not found:
        return DEFAULT_MATERIALITY
    _name, row = found
    topics = row[3]
    theme_key = _key(theme)
    if theme_key:
        for topic in topics:
            t = _key(topic)
            if t and (t in theme_key or theme_key in t):
                return "signature"
    return DEFAULT_MATERIALITY


def evidence_weight(evidence_type: str) -> float:
    """8.3: the relative weight of the kind of evidence behind an indicator score."""
    return EVIDENCE_WEIGHTS.get(_key(evidence_type), DEFAULT_EVIDENCE_WEIGHT)


def indicator_weight(evidence_type: str, materiality: str) -> float:
    """8.2.7 step 2: the applicable sector materiality factor times the evidence factor.

    Zero when the indicator is Not Applicable (7.2, 0.00x), which is what excludes it from
    scoring rather than scoring it zero."""
    return MATERIALITY_WEIGHTS.get(_key(materiality), 1.00) * evidence_weight(evidence_type)


def weighted_mean(pairs) -> float | None:
    """The weighted mean of (score, weight), or None when nothing carries any weight --
    which is how a Key Issue or Theme made only of Not Applicable indicators disappears
    instead of counting as zero."""
    total = sum(w for _s, w in pairs)
    if total <= 0:
        return None
    return sum(s * w for s, w in pairs) / total


def aggregate(indicators: list[dict]) -> dict:
    """8.2.7 steps 2-5, for one pillar: Weighted Indicator -> Key Issue -> Theme -> Pillar.

    Each indicator is {score, theme, key_issue, evidence_type, materiality}. Key Issues are
    the weighted mean of their indicators, Themes the weighted mean of their Key Issues, and
    the pillar the weighted mean of its Themes -- each level carrying the total weight of
    what it is made of, so a Theme with more material indicators counts for more.

    Returns {score, themes: [{theme, score, weight, key_issues: [...]}], applicable,
    excluded}. score is None when every indicator was Not Applicable."""
    themes: dict[str, dict[str, list]] = {}
    excluded = 0
    for ind in indicators:
        weight = indicator_weight(ind.get("evidence_type"), ind.get("materiality", DEFAULT_MATERIALITY))
        if weight <= 0:
            excluded += 1
            continue
        theme = str(ind.get("theme") or "Other")
        key_issue = str(ind.get("key_issue") or theme)
        themes.setdefault(theme, {}).setdefault(key_issue, []).append((float(ind.get("score") or 0.0), weight))

    theme_rows = []
    for theme, issues in themes.items():
        issue_rows = []
        for key_issue, pairs in issues.items():
            score = weighted_mean(pairs)
            if score is None:
                continue
            issue_rows.append({"key_issue": key_issue, "score": round(score, 2),
                               "weight": round(sum(w for _s, w in pairs), 4),
                               "indicators": len(pairs)})
        theme_score = weighted_mean([(r["score"], r["weight"]) for r in issue_rows])
        if theme_score is None:
            continue
        theme_rows.append({"theme": theme, "score": round(theme_score, 2),
                           "weight": round(sum(r["weight"] for r in issue_rows), 4),
                           "key_issues": issue_rows})

    pillar = weighted_mean([(r["score"], r["weight"]) for r in theme_rows])
    return {
        "score": None if pillar is None else round(pillar, 2),
        "themes": theme_rows,
        "applicable": sum(i["indicators"] for r in theme_rows for i in r["key_issues"]),
        "excluded": excluded,
    }


# --- 8.2.2 Forward-Looking / Transition Adjustment ---------------------------------------
TRANSITION_WEIGHTS = {
    "target_credibility": 0.20,
    "target_progress": 0.25,
    "transition_readiness": 0.25,
    "resilience": 0.15,
    "emerging_risk_preparedness": 0.15,
}
# (minimum score, adjustment), highest band first.
TRANSITION_BANDS = ((80, 2.0), (65, 1.0), (50, 0.0), (35, -1.0), (0, -2.0))

# --- 8.2.3 Controversy Adjustment --------------------------------------------------------
CONTROVERSY_WEIGHTS = {
    "severity": 0.20,
    "scale_of_impact": 0.20,
    "duration": 0.10,
    "recurrence": 0.15,
    "management_response": 0.15,
    "remediation": 0.20,
}
CONTROVERSY_BANDS = ((80, 0.0), (65, -0.5), (50, -1.0), (35, -2.0), (0, -3.0))


def _dimension_score(scores: dict, weights: dict) -> float | None:
    """The weighted 0-100 score of a set of assessment dimensions, or None when none was
    assessed -- an unassessed area must not read as zero and pull the rating down."""
    given = [(k, w) for k, w in weights.items() if isinstance(scores.get(k), (int, float))]
    if not given:
        return None
    total = sum(w for _k, w in given)
    return sum(float(scores[k]) * w for k, w in given) / total


def _band(score: float, bands) -> float:
    for minimum, adjustment in bands:
        if score >= minimum:
            return adjustment
    return bands[-1][1]


def transition(scores: dict | None) -> dict:
    """8.2.2: the Transition Score from its five dimensions and the adjustment it maps to.
    {score: None, adjustment: 0.0} when the assessment was not made."""
    value = _dimension_score(scores or {}, TRANSITION_WEIGHTS)
    if value is None:
        return {"score": None, "adjustment": 0.0}
    return {"score": round(value, 2), "adjustment": _band(value, TRANSITION_BANDS)}


def controversy(scores: dict | None) -> dict:
    """8.2.3: the Controversy Management Score and the adjustment it maps to. A higher score
    is a more favourable position, so the adjustment is downward or nothing."""
    value = _dimension_score(scores or {}, CONTROVERSY_WEIGHTS)
    if value is None:
        return {"score": None, "adjustment": 0.0}
    return {"score": round(value, 2), "adjustment": _band(value, CONTROVERSY_BANDS)}


# --- 8.2.5 Score Controls and Rating Safeguards ------------------------------------------
# Each safeguard caps the GRADE, whatever the score is (13.7). Ordered worst cap first.
SAFEGUARDS = {
    "critical_governance": ("B", "Critical Governance Safeguard: a material governance "
                                 "failure remains unresolved or inadequately addressed."),
    "critical_environmental_social": ("B", "Critical Environmental or Social Safeguard: a "
                                           "material impact is unresolved."),
    "severe_continuing_environmental_social": ("C", "Critical Environmental or Social "
                                                    "Safeguard: the impact is severe and "
                                                    "continuing, with inadequate remediation."),
    "material_regulatory": ("B", "Material Regulatory or Compliance Safeguard: a material "
                                 "breach or enforcement action affects the ESG profile."),
}
# Worst to best, so a cap can be compared against an assigned grade.
GRADE_ORDER = ("D", "C", "B", "B+", "A", "A+")


def safeguard_cap(triggered) -> tuple[str | None, list[str]]:
    """(the most restrictive grade cap, the reasons) for the safeguards that were triggered.
    (None, []) when none was."""
    caps, reasons = [], []
    for name in triggered or ():
        rule = SAFEGUARDS.get(name)
        if rule:
            caps.append(rule[0])
            reasons.append(rule[1])
    if not caps:
        return None, []
    return min(caps, key=GRADE_ORDER.index), reasons


def capped_grade(grade: str, cap: str | None) -> str:
    """The grade a safeguard allows: the assigned grade, or the cap when it is lower."""
    if not cap or grade not in GRADE_ORDER:
        return grade
    return cap if GRADE_ORDER.index(grade) > GRADE_ORDER.index(cap) else grade


def final_score(base: float, transition_adjustment: float, controversy_adjustment: float) -> float:
    """8.2.4: Base + Transition + Controversy, held inside the 0-100 scoring range."""
    return max(0.0, min(100.0, base + transition_adjustment + controversy_adjustment))
