# app/core/kpis.py
# KPI-coverage scoring shared by both calculators (user, 2026-09-11: score only on the
# basis of the KPIs; one AI call finds them; the report is scored on KPI coverage).
#
# Each scoring prompt lists its category's numbered evaluation points -- the KPIs (ESG
# reads them from esg_prompts, BFSI from its CRITERIA). KPI_FIELD is added to that prompt's
# response fields, so the one call that scores a page also names the KPIs the page proves,
# strongly or partially. Nothing else is asked of the model.
#
#   page     -> the KPIs it proves (shown in the page-scores export) and a page score
#   category -> coverage: for every KPI, the strongest proof anywhere in the report
#               (strong = 100, partial = 50, none = 0), averaged over all the KPIs
import re

from app.core.db import get_db

CATEGORIES = ("Environment", "Social", "Governance")

# No braces or %-placeholders: ESG .format()s the prompt, BFSI renders it with sprintf.
KPI_FIELD = (
    '- "kpis_strong": A list of the numbers of the evaluation points listed above that this text '
    'clearly proves with a specific policy, target, action or data.\n'
    '- "kpis_partial": A list of the numbers of the evaluation points listed above that this text '
    'only partly supports.\n'
    'Base the score only on these points. Use empty lists if the text proves none of them.'
)

STRENGTH = {"strong": 100.0, "partial": 50.0}


def with_kpi_field(prompt: str) -> str:
    """The scoring prompt with KPI_FIELD after its response fields (before "Text:").
    Apply it to the template, before the page text is filled in."""
    i = prompt.rfind("\n\nText:")
    if i == -1:
        return prompt + "\n" + KPI_FIELD
    return prompt[:i] + "\n" + KPI_FIELD + prompt[i:]


def parse_kpi_list(text: str) -> list[str]:
    """The numbered points ("1. Renewable energy ...") of a criteria list or a scoring
    prompt, in order. A prompt's response fields are "- " bullets and its page text sits
    after "Text:", so only the KPI list is read."""
    head = text.rsplit("\n\nText:", 1)[0]
    return [m.group(1).strip() for m in re.finditer(r"(?m)^\s*\d+\.\s+(.+?)\s*$", head)]


def load_kpi_lists() -> dict[str, list[str]]:
    """{category: [KPI names]} read fresh from esg_prompts on every call. The KPIs live in
    the database so they can grow without a code change; the next analysis uses them."""
    out = {}
    for cat in CATEGORIES:
        doc = get_db()["esg_prompts"].find_one({"category": cat})
        out[cat] = parse_kpi_list(doc["prompt"]) if doc and doc.get("prompt") else []
    return out


def _names(numbers, kpis: list[str]) -> list[str]:
    """KPI names for a list of point numbers; out of range, repeats and junk dropped."""
    names = []
    for n in numbers if isinstance(numbers, list) else []:
        try:
            i = int(str(n).strip().rstrip("."))
        except ValueError:
            continue
        if 1 <= i <= len(kpis) and kpis[i - 1] not in names:
            names.append(kpis[i - 1])
    return names


def kpi_strengths(parsed, kpis: list[str]) -> dict[str, float]:
    """{KPI name: 100 (strong) or 50 (partial)} from one scoring answer. A page scored 0
    proves nothing: the scorer found no evidence, so no KPI may stand beside that 0."""
    if not isinstance(parsed, dict):
        return {}
    try:
        if float(parsed.get("score")) == 0:
            return {}
    except (TypeError, ValueError):
        pass
    out = {name: STRENGTH["partial"] for name in _names(parsed.get("kpis_partial"), kpis)}
    out.update({name: STRENGTH["strong"] for name in _names(parsed.get("kpis_strong"), kpis)})
    return out


def kpis_from_response(parsed, kpis: list[str]) -> list[str]:
    """The export's "KPIs Present" for one page: strong ones first, then partial ones
    marked "(partial)", each in the prompt's own wording and order."""
    strengths = kpi_strengths(parsed, kpis)
    strong = [k for k in kpis if strengths.get(k) == STRENGTH["strong"]]
    partial = [f"{k} (partial)" for k in kpis if strengths.get(k) == STRENGTH["partial"]]
    return strong + partial


def page_sort_key(p):
    try:
        return (0, int(float(p)))
    except (TypeError, ValueError):
        return (1, str(p))


def coverage_detail(entries: list, kpis: list[str]) -> dict:
    """A category's KPI Assessment, shown in the detailed reports: every KPI with its best
    level anywhere in the report, the points that earns and the pages it was found on,
    plus the category score -- the average of the points, the same number coverage_score
    gives. entries is [(page_no, {KPI name: 100|50})], one per scored page."""
    best = {k: 0.0 for k in kpis}
    found_on = {k: [] for k in kpis}
    for page_no, strengths in entries:
        for k, v in strengths.items():
            if k not in best:
                continue
            best[k] = max(best[k], v)
            if page_no is not None and page_no not in found_on[k]:
                found_on[k].append(page_no)
    level = {STRENGTH["strong"]: "strong", STRENGTH["partial"]: "partial"}
    return {
        "score": sum(best.values()) / len(kpis) if kpis else 0.0,
        "kpis": [{"kpi": k, "level": level.get(best[k], "none"), "points": best[k],
                  "pages": sorted(found_on[k], key=page_sort_key)} for k in kpis],
    }


def coverage_score(page_strengths: list[dict], kpis: list[str]) -> float:
    """A category's score: every KPI takes its strongest proof across all pages (0 when
    no page proves it), averaged over the KPIs. Unrounded; callers round as their
    pipeline does."""
    if not kpis:
        return 0.0
    best = {k: 0.0 for k in kpis}
    for strengths in page_strengths:
        for k, v in strengths.items():
            if k in best and v > best[k]:
                best[k] = v
    return sum(best.values()) / len(kpis)
