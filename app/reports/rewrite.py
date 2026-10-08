# app/reports/rewrite.py -- the reason behind an edited KPI score, rewritten to fit it.
#
# An analyst who raises a KPI from 40 to 80 leaves the scoring call's reason -- written for
# 40 -- beside a score it no longer explains. The client wants the reason to follow the score
# (user, 2026-10-09): one short AI call per KPI changed, written from the page the KPI was
# found on, so it says what that page shows and nothing it does not. The page reference is
# kept, the written summary is not touched (tests/test_kpi_edit_keeps_text.py), and the
# analyst's own reason, where they typed one, always wins.
#
# The rewritten reasons live in report_edits.ai_reasons, keyed by the score they were
# written for, so saving the same edit again costs nothing and clearing the edit restores
# the scoring call's own text. Best effort by design: a failed call keeps the old reason and
# never fails the save.
import logging

from app.bfsi import store as bfsi_store
from app.esg import store as esg_store
from app.reports import summary

logger = logging.getLogger(__name__)

CATS = ("E", "S", "G")
ESG_CATEGORY = {"E": "Environment", "S": "Social", "G": "Governance"}
MAX_PAGE_CHARS = 8000

SYSTEM = (
    "You revise the evidence reason for ONE ESG KPI after an analyst changed its score.\n"
    "Return JSON only: {\"reason\": \"...\"}.\n"
    "\n"
    "The reason is one or two sentences, at most 60 words, in the style of the previous "
    "reason: what the page shows about this KPI, and why that supports the new score out of "
    "100 (100 = fully met with measured, verified results; 80 = strong, specific evidence; "
    "60 = clear but partial; 40 = mentioned with little substance; 20 = weak or poor).\n"
    "\n"
    "Only state what the page text supports. Never invent figures, policies, targets, "
    "certifications or assurance. If the page does not fully support the new score, say "
    "what the page does show and note that the analyst assessed it at the new score.\n"
    "\n"
    "Do not write any page number, the KPI's name as a label, or markdown."
)


def _user(kind: str, kpi: str, pillar: str, old_score, old_reason: str, new_score, page_text: str) -> str:
    return (
        f"Calculator: {'BFSI borrower assessment' if kind == 'bfsi' else 'ESG rating'}\n"
        f"Pillar: {pillar}\nKPI: {kpi}\n"
        f"Previous score: {old_score}\nPrevious reason: {old_reason}\n"
        f"New score set by the analyst: {new_score}\n\n"
        f"Page text the KPI was found on:\n{page_text[:MAX_PAGE_CHARS]}"
    )


def _esg_page_text(doc: dict, base: dict, page) -> str:
    """The text of one page of the analysis run behind this report, read on its own: a run
    holds every page's text (~5.5 MB) and the editor must not read it whole."""
    from app.reports.editing import _esg_report_doc
    run = _esg_report_doc(doc, base, {"analysis": {"$elemMatch": {"page_no": {"$in": [page, str(page)]}}}})
    records = (run or {}).get("analysis") or []
    return "\n".join(str(r.get("text") or "") for r in records if isinstance(r, dict)).strip()


def _bfsi_page_text(doc: dict, page) -> str:
    rows = bfsi_store.get_cached_pages(doc.get("text_sha256") or "") or []
    return "\n".join(str(r.get("text") or "") for r in rows
                     if isinstance(r, dict) and str(r.get("page_no")) == str(page)).strip()


def page_text(kind: str, doc: dict, base: dict, page) -> str:
    return _esg_page_text(doc, base, page) if kind == "esg" else _bfsi_page_text(doc, page)


def _rows(kind: str, base: dict, cat: str) -> dict:
    holder = base if kind == "esg" else (base.get("ai_analysis") or {})
    detail = ((holder.get("kpi_coverage") or {}) if isinstance(holder, dict) else {}).get(ESG_CATEGORY[cat]) or {}
    return {r["kpi"]: r for r in detail.get("kpis") or [] if isinstance(r, dict) and r.get("kpi")}


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


def reasons_for(kind: str, doc: dict, base: dict, edits: dict, previous: dict | None) -> dict:
    """report_edits.ai_reasons for these edits: {cat: {kpi: {score, reason}}}.

    One entry per edited KPI that has evidence to write from; written once per score,
    reused from `previous` while the score stands, dropped when the edit is cleared."""
    previous = previous or {}
    own = (edits.get("fields") or {}).get("kpi_reasons") or {}
    out = {c: {} for c in CATS}
    configured = None
    for cat in CATS:
        rows = _rows(kind, base, cat)
        for kpi, score in (edits.get("kpi_scores") or {}).get(cat, {}).items():
            row = rows.get(kpi)
            evidence = (row or {}).get("evidence") or {}
            if row is None or (own.get(cat) or {}).get(kpi) or _same(score, row.get("score")):
                continue
            if not evidence.get("reason") or evidence.get("page") is None:
                continue  # never mentioned in the report: "Not addressed" stays (user, 2026-10-09)
            kept = (previous.get(cat) or {}).get(kpi)
            if isinstance(kept, dict) and _same(kept.get("score"), score) and kept.get("reason"):
                out[cat][kpi] = kept
                continue
            if configured is None:
                configured = summary.ai_configured(kind)
                if not configured:
                    logger.warning("kpi reason not rewritten for %s %s: no AI configured", kind, doc.get("_id"))
            if not configured:
                continue
            try:
                text = page_text(kind, doc, base, evidence["page"])
                if not text:
                    raise ValueError(f"no text stored for page {evidence['page']}")
                answer = summary._ask_ai(kind, SYSTEM, _user(
                    kind, kpi, ESG_CATEGORY[cat], row.get("score"), str(evidence["reason"]), score, text))
                reason = str((answer or {}).get("reason") or "").strip() if isinstance(answer, dict) else ""
                if not reason:
                    raise ValueError("answer carried no reason")
            except Exception as e:
                logger.warning("kpi reason not rewritten for %s %s / %s: %s", kind, doc.get("_id"), kpi, e)
                continue
            out[cat][kpi] = {"score": float(score), "reason": reason}
    return out
