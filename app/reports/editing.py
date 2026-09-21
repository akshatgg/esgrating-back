# app/reports/editing.py -- editable ESG/BFSI reports (docs/specs/2026-09-10-editable-reports-design.md).
#
# The server is the single source of truth for the maths: every recomputation below goes
# through the analysis' own functions (ESG: category_average / composite_score /
# evaluate_score from app.esg.pipeline; BFSI: avg_scores, bfsi_overall, bfsi_grade,
# bfsi_recommendation). Nothing here re-implements a formula.
#
# Effective values are always derived from the *original* AI result (report_original once
# a save has happened, else the stored fields) plus the edits, so saving is idempotent and
# a reset is a plain restore of the snapshot.
import ast
import copy
import math
import re

from app.bfsi.options import INDUSTRIES
from app.bfsi.pipeline import avg_scores
from app.bfsi.scoring import bfsi_grade, bfsi_overall, bfsi_recommendation, is_kpi_scored
from app.core.errors import UserError
from app.esg import scoring as esg_scoring
from app.esg import store as esg_store
from app.esg.pipeline import category_average, composite_score, evaluate_score

KINDS = ("esg", "bfsi")
CATS = ("E", "S", "G")

ESG_CATEGORY = {"E": "Environment", "S": "Social", "G": "Governance"}
ESG_PREFIX = {"E": "environmental", "S": "social", "G": "governance"}
BFSI_SCORE_KEY = {"E": "e_score", "S": "s_score", "G": "g_score"}

# Fields the BFSI snapshot covers (spec: ai_analysis + e/s/g/overall/grade).
BFSI_SNAPSHOT_KEYS = ("ai_analysis", "e_score", "s_score", "g_score", "overall_score", "grade")

# --- allow-lists -------------------------------------------------------------------------

_DETAILED_HEADINGS = [
    "report_title", "marks_by_pillar", "rating_summary", "top_risks", "top_improvements",
    "climate_risk", "governance_summary", "signals", "scoring_rationale", "score_scale",
    "recommended_decision",
]
_SHEET_HEADINGS = [
    "esg_rating_report", "rating_summary", "result", "score_summary", "esg_score",
    "env_kpis", "soc_kpis", "gov_kpis", "sebi_line",
]
# BFSI renders both the detailed report and the one-pager (the ESG-style sheet), and
# both have a "Rating Summary" heading: the one-pager's gets its own key so editing
# one never renames the other. Every other key is used by exactly one BFSI sheet.
_BFSI_ONEPAGER_HEADINGS = [
    "onepager_rating_summary" if k == "rating_summary" else k for k in _SHEET_HEADINGS
]


def _unique(items):
    return list(dict.fromkeys(items))


HEADING_KEYS = {
    "bfsi": _unique(_DETAILED_HEADINGS + _BFSI_ONEPAGER_HEADINGS),
    # ESG renders the sheet, plus the page-scores panel and the score scale.
    # ESG renders the sheet, the detailed report and the score scale.
    "esg": _unique(_SHEET_HEADINGS + [
        "report_title", "scoring_rationale", "score_scale",
        "marks_by_pillar", "key_rating_drivers", "strengths_heading", "weaknesses_heading",
    ]),
}

_SHORT, _LONG, _LIST, _CATLISTS, _REASONS = "short", "long", "list", "catlists", "reasons"
# A fixed set of values, the first one the default. A choice is how the sheet is told
# to show NOTHING: blank text is 'no override' everywhere here (see normalize_edits),
# so text alone could never hide anything (user, 2026-09-20).
_CHOICE = "choice"
# The written rating, so an analyst can correct what the AI wrote: a paragraph block per
# pillar, and the drivers as a list of {headline, detail} (user, 2026-09-21).
_CATTEXT, _DRIVERS = "cattext", "drivers"
CHOICES = {"header_slot": ("text", "logo", "none")}

FIELD_SPECS = {
    "esg": {
        "company": _SHORT, "sector": _SHORT, "industry": _SHORT, "fy": _SHORT, "report_date": _SHORT,
        "environmental_top_keywords": _LIST, "social_top_keywords": _LIST,
        "governance_top_keywords": _LIST,
        "reasons": _REASONS,
        # The sheet's top-right corner (its text, an uploaded logo, or nothing) and
        # the optional line along the bottom.
        "header_slot": _CHOICE, "footer_note": _LONG,
        # The written rating (app/reports/summary.py), correctable in the report.
        "executive_summary": _LONG, "favourable_factors": _LONG, "constraints": _LONG,
        "rating_rationale": _LONG, "pillar_narratives": _CATTEXT,
        "strengths": _DRIVERS, "weaknesses": _DRIVERS,
    },
    "bfsi": {
        "company": _SHORT, "sector": _SHORT, "industry": _SHORT, "fy": _SHORT, "report_date": _SHORT,
        "top_risks": _LIST, "top_improvements": _LIST,
        "climate_risk": _LONG, "governance_summary": _LONG, "recommendation": _SHORT,
        "keywords": _CATLISTS, "negative_keywords": _CATLISTS,
        "reasons": _REASONS,
        # The sheet's top-right corner (its text, an uploaded logo, or nothing) and
        # the optional line along the bottom.
        "header_slot": _CHOICE, "footer_note": _LONG,
        # The written rating (app/reports/summary.py), correctable in the report.
        "executive_summary": _LONG, "favourable_factors": _LONG, "constraints": _LONG,
        "rating_rationale": _LONG, "pillar_narratives": _CATTEXT,
        "strengths": _DRIVERS, "weaknesses": _DRIVERS,
    },
}
FIELD_KEYS = {kind: list(spec) for kind, spec in FIELD_SPECS.items()}

PAGES_LOCKED = "Page scores can't be edited for this pillar; edit the pillar score instead."
KPIS_LOCKED = "KPI scores can't be edited for this pillar; edit the pillar score instead."

HEADING_MAX = 200
SHORT_MAX = 200
LONG_MAX = 5000
ITEM_MAX = 500
LIST_MAX = 20

# --- validation ----------------------------------------------------------------------------

# Anything that could open a tag (<b, </p, <!--, <?php). Stray "<" / ">" in prose ("< 40")
# stays allowed; the web renders text, never HTML, but PDFs/mails must not carry markup.
_HTML_RE = re.compile(r"<\s*[A-Za-z!/?]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINEBREAK_RE = re.compile(r"[\r\n\t]")


def _text(value, where: str, max_len: int, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise UserError(f"{where} must be text.")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) > max_len:
        raise UserError(f"{where} must be at most {max_len} characters.")
    if _CTRL_RE.search(value) or (not multiline and _LINEBREAK_RE.search(value)):
        raise UserError(f"{where} contains characters that are not allowed.")
    if _HTML_RE.search(value):
        raise UserError(f"{where} must be plain text (no HTML).")
    return value


def _text_list(value, where: str) -> list[str]:
    if not isinstance(value, list):
        raise UserError(f"{where} must be a list.")
    if len(value) > LIST_MAX:
        raise UserError(f"{where} can have at most {LIST_MAX} items.")
    items = [_text(v, f"{where} item", ITEM_MAX) for v in value]
    return [i for i in items if i != ""]


def _score(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise UserError(f"{where} must be a number between 0 and 100.")
    return max(0.0, min(100.0, float(value)))


def _kpi_score(value, where: str) -> float:
    """A KPI score: a number from 0 to 100. Out of range is refused, not clamped."""
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not 0 <= value <= 100):
        raise UserError(f"{where} must be a number between 0 and 100.")
    return float(value)


def _as_dict(value, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise UserError(f"{where} must be an object.")
    return value


def _cat(cat, where: str) -> str:
    if cat not in CATS:
        raise UserError(f"{where}: unknown category {cat!r} (use E, S or G).")
    return cat


def empty_edits() -> dict:
    return {
        "headings": {},
        "fields": {},
        "page_scores": {c: {} for c in CATS},
        "kpi_scores": {c: {} for c in CATS},
        "pillar_overrides": {c: None for c in CATS},
        "logo": None,
        "corner_logo": None,
    }


# Keys of the body besides the edits proper: ignored (the web may send back the edits
# object it got from GET). The logo is only ever changed through the logo routes, so a
# stale edits object can never undo an upload.
_IGNORED_BODY_KEYS = {"logo", "corner_logo", "updated_at", "updated_by"}


def normalize_edits(kind: str, payload, ctx: dict) -> dict:
    """Validate a client edits object against the allow-lists and this report's pages.
    Returns the canonical edits (without logo). Raises UserError (422) on anything off."""
    if not isinstance(payload, dict):
        raise UserError("The edits must be a JSON object.")
    unknown = set(payload) - {"headings", "fields", "page_scores", "kpi_scores", "pillar_overrides"} - _IGNORED_BODY_KEYS
    if unknown:
        raise UserError(f"Unknown key: {sorted(unknown)[0]}")

    out = empty_edits()
    pages = ctx["pages"]
    page_keys = {c: {str(p["page"]) for p in pages[c]} for c in CATS}
    # Page overrides are keyed by page number. A number can repeat (BFSI splits a long
    # page into several scoring units; ESG page_no can repeat): the override then
    # applies to every row with that number, so it is only a no-op when it equals the
    # AI score of all of them.
    originals_by_key = {c: {} for c in CATS}
    for c in CATS:
        for p in pages[c]:
            originals_by_key[c].setdefault(str(p["page"]), []).append(p["score"])

    # Empty text (after strip) and empty lists are "no override": the key is dropped,
    # so the report falls back to the AI value (and the BFSI recommendation keeps
    # following the grade) instead of showing a blank.
    for key, value in _as_dict(payload.get("headings"), "headings").items():
        if key not in HEADING_KEYS[kind]:
            raise UserError(f"Unknown heading: {key}")
        value = _text(value, f"Heading {key!r}", HEADING_MAX)
        if value != "":
            out["headings"][key] = value

    specs = FIELD_SPECS[kind]
    for key, value in _as_dict(payload.get("fields"), "fields").items():
        spec = specs.get(key)
        if spec is None:
            raise UserError(f"Unknown field: {key}")
        if value is None:
            continue
        if spec in (_SHORT, _LONG):
            text = _text(value, f"Field {key!r}", SHORT_MAX if spec == _SHORT else LONG_MAX,
                         multiline=spec == _LONG)
            if text != "":
                out["fields"][key] = text
        elif spec == _CHOICE:
            text = _text(value, f"Field {key!r}", SHORT_MAX)
            allowed = CHOICES[key]
            if text != "" and text not in allowed:
                raise UserError(f"Field {key!r} must be one of: {', '.join(allowed)}.")
            # The first value is the default: it needs no override stored.
            if text not in ("", allowed[0]):
                out["fields"][key] = text
        elif spec == _LIST:
            items = _text_list(value, f"Field {key!r}")
            if items:
                out["fields"][key] = items
        elif spec == _CATLISTS:
            cats = {}
            for cat, items in _as_dict(value, f"Field {key!r}").items():
                cat = _cat(cat, f"Field {key!r}")
                items = _text_list(items, f"Field {key}.{cat}")
                if items:
                    cats[cat] = items
            if cats:
                out["fields"][key] = cats
        elif spec == _CATTEXT:
            cats = {}
            for cat, block in _as_dict(value, f"Field {key!r}").items():
                cat = _cat(cat, f"Field {key!r}")
                block = _text(block, f"Field {key}.{cat}", LONG_MAX, multiline=True)
                if block != "":
                    cats[cat] = block
            if cats:
                out["fields"][key] = cats
        elif spec == _DRIVERS:
            if not isinstance(value, list):
                raise UserError(f"Field {key!r} must be a list.")
            if len(value) > LIST_MAX:
                raise UserError(f"Field {key!r} can have at most {LIST_MAX} items.")
            items = []
            for item in value:
                item = _as_dict(item, f"Field {key!r} item")
                unknown = set(item) - {"headline", "detail"}
                if unknown:
                    raise UserError(f"Field {key!r} item: unknown key {sorted(unknown)[0]!r}.")
                headline = _text(item.get("headline") or "", f"Field {key!r} headline", SHORT_MAX)
                detail = _text(item.get("detail") or "", f"Field {key!r} detail", LONG_MAX, multiline=True)
                if headline or detail:
                    items.append({"headline": headline, "detail": detail})
            if items:
                out["fields"][key] = items
        elif spec == _REASONS:
            cats = {}
            for cat, texts in _as_dict(value, "Field 'reasons'").items():
                _cat(cat, "Field 'reasons'")
                per_page = {}
                for page, text in _as_dict(texts, f"reasons.{cat}").items():
                    if str(page) not in page_keys[cat]:
                        raise UserError(f"reasons.{cat}: page {page} is not in this report.")
                    text = _text(text, f"Reason for {cat} page {page}", LONG_MAX, multiline=True)
                    if text != "":
                        per_page[str(page)] = text
                if per_page:
                    cats[cat] = per_page
            if cats:
                out["fields"][key] = cats

    for cat, scores in _as_dict(payload.get("page_scores"), "page_scores").items():
        _cat(cat, "page_scores")
        scores = _as_dict(scores, f"page_scores.{cat}")
        if scores and not ctx["pages_editable"][cat]:
            raise UserError(PAGES_LOCKED)
        for page, value in scores.items():
            page = str(page)
            if page not in page_keys[cat]:
                raise UserError(f"page_scores.{cat}: page {page} is not in this report.")
            value = _score(value, f"Score for {cat} page {page}")
            # An "override" equal to the AI's own score is no override: dropping it keeps
            # the pillar at its exact stored value instead of a recomputed average.
            originals = originals_by_key[cat][page]
            if all(o is not None and float(o) == value for o in originals):
                continue
            out["page_scores"][cat][page] = value

    # KPI scores (0-100, ESG) are keyed by KPI name, as the KPI Assessment lists them.
    for cat, scores in _as_dict(payload.get("kpi_scores"), "kpi_scores").items():
        _cat(cat, "kpi_scores")
        scores = _as_dict(scores, f"kpi_scores.{cat}")
        if scores and not ctx["kpis_editable"][cat]:
            raise UserError(KPIS_LOCKED)
        originals = {r["kpi"]: r["score"] for r in ctx["kpis"][cat]}
        for name, value in scores.items():
            if name not in originals:
                raise UserError(f"kpi_scores.{cat}: {name!r} is not a KPI of this report.")
            value = _kpi_score(value, f"Score for {cat} KPI {name!r}")
            if _same(value, originals[name]):
                continue  # equal to the AI's score: no override
            out["kpi_scores"][cat][name] = value

    for cat, value in _as_dict(payload.get("pillar_overrides"), "pillar_overrides").items():
        _cat(cat, "pillar_overrides")
        out["pillar_overrides"][cat] = None if value is None else _score(value, f"{cat} pillar score")

    return out


def edits_have_content(edits: dict | None) -> bool:
    """True when the edits change anything besides the logo."""
    if not edits:
        return False
    return bool(
        edits.get("headings")
        or edits.get("fields")
        or any((edits.get("page_scores") or {}).get(c) for c in CATS)
        or any((edits.get("kpi_scores") or {}).get(c) for c in CATS)
        or any((edits.get("pillar_overrides") or {}).get(c) is not None for c in CATS)
    )


def stored_edits(doc: dict) -> dict:
    """The submission's report_edits, filled out to the full shape."""
    raw = doc.get("report_edits") or {}
    out = empty_edits()
    out["headings"] = dict(raw.get("headings") or {})
    out["fields"] = copy.deepcopy(raw.get("fields") or {})
    for c in CATS:
        out["page_scores"][c] = dict((raw.get("page_scores") or {}).get(c) or {})
        out["kpi_scores"][c] = dict((raw.get("kpi_scores") or {}).get(c) or {})
        out["pillar_overrides"][c] = (raw.get("pillar_overrides") or {}).get(c)
    out["logo"] = raw.get("logo")
    out["corner_logo"] = raw.get("corner_logo")
    out["updated_at"] = raw.get("updated_at")
    out["updated_by"] = raw.get("updated_by")
    return out


def is_edited(doc: dict) -> bool:
    edits = doc.get("report_edits")
    return edits_have_content(edits) or any((edits or {}).get(k) for k in ("logo", "corner_logo"))


# --- report context: original values and page lists ---------------------------------------

def has_report(kind: str, doc: dict) -> bool:
    return bool(doc.get("final")) if kind == "esg" else bool(doc.get("ai_analysis"))


def snapshot(kind: str, doc: dict) -> dict:
    """Deep copy of the AI result as currently stored (what report_original holds)."""
    if kind == "esg":
        return copy.deepcopy(doc["final"])
    return {k: copy.deepcopy(doc[k]) for k in BFSI_SNAPSHOT_KEYS if k in doc}


def restore_update(kind: str, original: dict) -> dict:
    """The $set/$unset that put the stored report fields back to `original`."""
    if kind == "esg":
        return {"$set": {"final": copy.deepcopy(original)}, "$unset": {}}
    return {
        "$set": {k: copy.deepcopy(original[k]) for k in BFSI_SNAPSHOT_KEYS if k in original},
        "$unset": {k: "" for k in BFSI_SNAPSHOT_KEYS if k not in original},
    }


def _esg_report_doc(doc: dict, base_final: dict) -> dict | None:
    """The esg_report run behind this submission: latest for its company + file, preferring
    the one whose stored composite matches the report being edited."""
    cid = doc.get("company_id")
    if not cid:
        return None
    query = {"company_id": cid}
    if doc.get("original_filename"):
        query["filename"] = doc["original_filename"]
    runs = list(esg_store.esg_collection().find(query).sort("_id", -1).limit(20))
    target = base_final.get("composite_score")
    for run in runs:
        if run.get("composite_score") == target:
            return run
    return runs[0] if runs else None


def _esg_page(record: dict) -> tuple:
    """(score, reason) the way aggregate_scores reads a page: ast.literal_eval of the LLM
    output, score defaulting to 0; unparseable output (skipped by the pipeline) -> None.
    A KPI-scored page shows its page_score instead of the AI's own number."""
    raw = record.get("analysis")
    try:
        parsed = ast.literal_eval(raw) if isinstance(raw, str) else raw
    except Exception:
        return None, ""
    if not isinstance(parsed, dict):
        return None, ""
    score = parsed.get("score", 0)
    if isinstance(record.get("page_score"), (int, float)):
        score = record["page_score"]  # KPI-scored runs: the average of the page's KPI scores
    if not isinstance(score, (int, float)):
        score = None
    reason = parsed.get("reason", "")
    return score, reason if isinstance(reason, str) else str(reason)


def _page_no(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _pages(kind: str, doc: dict, base: dict) -> dict:
    """Per category, in the stored (= averaging) order: [{page, score, reason, idx}]."""
    pages = {c: [] for c in CATS}
    if kind == "esg":
        run = _esg_report_doc(doc, base)
        records = (run or {}).get("analysis") or []
        by_name = {v: k for k, v in ESG_CATEGORY.items()}
        for idx, rec in enumerate(records):
            cat = by_name.get(rec.get("category")) if isinstance(rec, dict) else None
            if cat is None:
                continue
            score, reason = _esg_page(rec)
            pages[cat].append({"page": _page_no(rec.get("page_no")), "score": score, "reason": reason, "idx": idx})
        return pages

    reasons = (base.get("ai_analysis") or {}).get("reasons")
    if not isinstance(reasons, dict):
        return pages
    for cat in CATS:
        entries = reasons.get(cat)
        if not isinstance(entries, list):
            continue
        for idx, r in enumerate(entries):
            if isinstance(r, dict):
                score = r.get("score")
                pages[cat].append({
                    "page": _page_no(r.get("page")),
                    "score": score if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                    "reason": str(r.get("reason") or ""),
                    "idx": idx,
                })
            else:
                pages[cat].append({"page": 0, "score": None, "reason": str(r), "idx": idx})
    return pages


def _same(a, b) -> bool:
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(float(a) - float(b)) < 1e-9


def _pages_editable(kind: str, base: dict, pages: dict) -> dict:
    """Per category: does the page list reproduce the stored pillar score?

    Recomputing a pillar from edited pages is only sound when the unedited pages
    average to exactly what the analysis stored. That is not guaranteed:
      - BFSI: bfsi_analyze averages every scored unit, but `reasons` (our page list)
        keeps only the units that came back with a non-empty reason.
      - ESG: the page list comes from the matching esg_report run, which can be missing
        or belong to a different run.
    When the averages differ, the category's pages are read-only (the pillar score can
    still be overridden directly)."""
    out = {}
    for cat in CATS:
        scores = [p["score"] for p in pages[cat]]
        if kind == "esg":
            stored = base.get(f"{ESG_PREFIX[cat]}_score")
            average = category_average([s for s in scores if s is not None])
        else:
            stored = base.get(BFSI_SCORE_KEY[cat])
            average = avg_scores([{"score": s} for s in scores])
        out[cat] = bool(pages[cat]) and _same(average, stored)
    return out


def _kpis(kind: str, base: dict) -> dict:
    """Per category: the KPI Assessment rows [{kpi, score (0-100), pages}]."""
    out = {c: [] for c in CATS}
    holder = base if kind == "esg" else (base.get("ai_analysis") or {})
    coverage = holder.get("kpi_coverage") if isinstance(holder, dict) else None
    if not isinstance(coverage, dict):
        return out
    for cat in CATS:
        detail = coverage.get(ESG_CATEGORY[cat])
        rows = detail.get("kpis") if isinstance(detail, dict) else None
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and isinstance(r.get("kpi"), str):
                out[cat].append({"kpi": r["kpi"], "score": esg_scoring.kpi_best(r), "pages": r.get("pages") or []})
    return out


def _kpis_editable(kind: str, base: dict, kpis: dict) -> dict:
    """Per category: do the KPI scores reproduce the stored pillar score? Only then can a
    KPI edit recompute the pillar soundly (as _pages_editable for pages)."""
    out = {}
    for cat in CATS:
        best = {r["kpi"]: r["score"] for r in kpis[cat]}
        key = f"{ESG_PREFIX[cat]}_score" if kind == "esg" else BFSI_SCORE_KEY[cat]
        out[cat] = bool(best) and _same(esg_scoring.category_score(best), base.get(key))
    return out


def load_context(kind: str, doc: dict) -> dict:
    base = copy.deepcopy(doc["report_original"]) if doc.get("report_original") else snapshot(kind, doc)
    pages = _pages(kind, doc, base)
    kpis = _kpis(kind, base)
    return {"base": base, "pages": pages, "pages_editable": _pages_editable(kind, base, pages),
            "kpis": kpis, "kpis_editable": _kpis_editable(kind, base, kpis)}


# --- effective computation -----------------------------------------------------------------

def _f(value):
    return None if value is None else float(value)


def _effective_pages(ctx: dict, edits: dict) -> dict:
    """Per category, stored order: page entries with the effective score/reason applied."""
    out = {}
    reason_edits = (edits["fields"].get("reasons") or {})
    for cat in CATS:
        scores = edits["page_scores"][cat]
        texts = reason_edits.get(cat) or {}
        rows = []
        for p in ctx["pages"][cat]:
            key = str(p["page"])
            rows.append({
                **p,
                "eff_score": scores.get(key, p["score"]),
                "eff_reason": texts.get(key, p["reason"]),
                "score_edited": key in scores,
                "reason_edited": key in texts,
            })
        out[cat] = rows
    return out


def _pages_response(eff_pages: dict) -> dict:
    return {
        cat: [
            {"page": r["page"], "score": _f(r["eff_score"]), "original_score": _f(r["score"]), "reason": r["eff_reason"]}
            for r in sorted(rows, key=lambda r: r["page"])
        ]
        for cat, rows in eff_pages.items()
    }


def _effective_kpis(ctx: dict, edits: dict) -> dict:
    """Per category: KPI rows with the effective score and the AI's original."""
    return {
        cat: [{"kpi": r["kpi"], "score": edits["kpi_scores"][cat].get(r["kpi"], r["score"]),
               "original_score": r["score"], "pages": r["pages"]} for r in ctx["kpis"][cat]]
        for cat in CATS
    }


def _pillar(cat: str, rows: list, edits: dict, original_score, average):
    """Spec step 3: override > recomputed page average (only if a page was edited) > stored."""
    override = edits["pillar_overrides"][cat]
    if override is not None:
        return override
    if any(r["score_edited"] for r in rows):
        return average([r["eff_score"] for r in rows])
    return original_score


def compute(kind: str, doc: dict, ctx: dict, edits: dict, logo_url: str | None = None,
            corner_logo_url: str | None = None) -> dict:
    """{effective, pages, stored} where `stored` is the $set that writes the effective
    values into the submission's own report fields."""
    eff_pages = _effective_pages(ctx, edits)
    eff_kpis = _effective_kpis(ctx, edits)
    fields = edits["fields"]
    manual = {c: edits["pillar_overrides"][c] is not None for c in CATS}
    headings = dict(edits["headings"])
    base = ctx["base"]

    if kind == "esg":
        final = copy.deepcopy(base)
        for cat in CATS:
            key = f"{ESG_PREFIX[cat]}_score"
            final[key] = _pillar(
                cat, eff_pages[cat], edits, base.get(key),
                # aggregate_scores skips pages whose output did not parse (score None).
                lambda scores: category_average([s for s in scores if s is not None]),
            )
            if edits["kpi_scores"][cat]:
                # Edited KPI scores: the KPI Assessment shows them and, unless the pillar
                # is set by hand, the pillar is their category score.
                name = ESG_CATEGORY[cat]
                detail = esg_scoring.rescore_category(
                    final["kpi_coverage"][name], {r["kpi"]: r["score"] for r in eff_kpis[cat]})
                final["kpi_coverage"][name] = detail
                if edits["pillar_overrides"][cat] is None:
                    final[key] = detail["score"]
        if isinstance(final.get("kpi_coverage"), dict):
            # A pillar typed by hand: the KPI Assessment carries it beside the KPI total, so
            # the detailed report, CSV and summary all show the same pillar score.
            for cat in CATS:
                esg_scoring.mark_pillar(final["kpi_coverage"].get(ESG_CATEGORY[cat]), final[f"{ESG_PREFIX[cat]}_score"])
        if any(final[f"{ESG_PREFIX[c]}_score"] != base.get(f"{ESG_PREFIX[c]}_score") for c in CATS):
            # Each report keeps the weights it was scored with (older reports: 30/30/40).
            final["composite_score"] = composite_score(
                final["environmental_score"], final["social_score"], final["governance_score"],
                esg_scoring.weights_for(base),
            )
        for key in ("environmental", "social", "governance", "composite"):
            performance, label = evaluate_score(final[f"{key}_score"])
            final[f"{key}_score_performance"] = performance
            final[f"{key}_score_performance_label"] = label
        for key in ("sector", "industry", "report_date",
                    "environmental_top_keywords", "social_top_keywords", "governance_top_keywords"):
            if key in fields:
                final[key] = copy.deepcopy(fields[key])
        effective = {
            "final": final,
            "company": fields.get("company", doc.get("company_name")),
            "fy": fields.get("fy", doc.get("report_year")),
            "headings": headings,
            "logo_url": logo_url,
            "corner_logo_url": corner_logo_url,
            "pillar_manual": manual,
        }
        return {"effective": effective, "pages": _pages_response(eff_pages), "kpis": eff_kpis,
                "stored": {"final": final}}

    # BFSI
    ai = copy.deepcopy(base["ai_analysis"])
    scores = {}
    for cat in CATS:
        key = BFSI_SCORE_KEY[cat]
        scores[cat] = float(_pillar(
            cat, eff_pages[cat], edits, base.get(key),
            lambda vals: avg_scores([{"score": v} for v in vals]),
        ))
        if edits["kpi_scores"][cat]:
            # Edited KPI scores: the KPI Assessment shows them and, unless the pillar is
            # set by hand, the pillar is their category score.
            name = ESG_CATEGORY[cat]
            detail = esg_scoring.rescore_category(
                ai["kpi_coverage"][name], {r["kpi"]: r["score"] for r in eff_kpis[cat]})
            ai["kpi_coverage"][name] = detail
            if edits["pillar_overrides"][cat] is None:
                scores[cat] = float(detail["score"])
        if scores[cat] != base.get(key):
            ai[key] = scores[cat]
        if isinstance(ai.get("kpi_coverage"), dict):
            esg_scoring.mark_pillar(ai["kpi_coverage"].get(ESG_CATEGORY[cat]), scores[cat])
    reasons = ai.get("reasons")
    for cat in CATS:
        for r in eff_pages[cat]:
            if not (r["score_edited"] or r["reason_edited"]):
                continue
            entry = reasons[cat][r["idx"]]
            if isinstance(entry, dict):
                entry["score"] = _f(r["eff_score"])
                entry["reason"] = r["eff_reason"]
            elif r["score_edited"]:
                reasons[cat][r["idx"]] = {"page": 0, "score": _f(r["eff_score"]), "reason": r["eff_reason"]}
            else:
                reasons[cat][r["idx"]] = r["eff_reason"]
    for key in ("top_risks", "top_improvements", "climate_risk", "governance_summary"):
        if key in fields:
            ai[key] = copy.deepcopy(fields[key])
    for key in ("keywords", "negative_keywords"):
        if key in fields:
            merged = ai.get(key) if isinstance(ai.get(key), dict) else {}
            merged.update(copy.deepcopy(fields[key]))
            ai[key] = merged

    # KPI-scored reports grade on whole numbers, like the ESG calculator.
    whole = is_kpi_scored(base.get("ai_analysis"))
    overall = bfsi_overall(scores["E"], scores["S"], scores["G"], doc.get("loan_type", ""), whole)
    industry = doc.get("industry", "")
    effective = {
        "ai_analysis": ai,
        "e_score": scores["E"],
        "s_score": scores["S"],
        "g_score": scores["G"],
        "overall": overall,
        "recommendation": fields.get("recommendation", bfsi_recommendation(overall["grade"])),
        "grades": {c: bfsi_grade(scores[c], whole) for c in CATS},
        "pillar_manual": manual,
        "company": fields.get("company", doc.get("borrower_name")),
        "sector": fields.get("sector", doc.get("sub_sector")),
        "industry": fields.get("industry", INDUSTRIES.get(industry, {}).get("label", industry)),
        "fy": fields.get("fy"),
        "report_date": fields.get("report_date"),
        "headings": headings,
        "logo_url": logo_url,
        "corner_logo_url": corner_logo_url,
    }
    stored = {
        "e_score": scores["E"],
        "s_score": scores["S"],
        "g_score": scores["G"],
        "overall_score": overall["overall"],
        "grade": overall["grade"],
        "ai_analysis": ai,
    }
    return {"effective": effective, "pages": _pages_response(eff_pages), "kpis": eff_kpis, "stored": stored}
