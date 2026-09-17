# app/reports/summary.py -- the ESG Rating Summary (.docx) for ESG and BFSI reports.
#
# Built from the client's Word template (app/reports/templates/rating_summary_template.docx),
# so fonts, colours and table styles match it. Three parts of the template are removed at
# the user's request (2026-09-17): the consistency / verification / timeliness data-quality
# rows, the KPI "Weight / Importance" column, and the Transition & Controversy section. The
# developer appendix is never part of the output.
#
# Every number comes from the stored scores (app/esg/scoring.py): pillar scores, KPI scores,
# sub-pillar (theme) scores = average of the sub-pillar's KPI scores, completeness = share of
# KPIs found in the report, specificity = share of found KPIs scored 81-100. Only the prose
# (executive summary, pillar narratives, strengths, weaknesses, priorities, rationale,
# interpretation) is written by one AI call, given only those numbers and the stored page
# reasons. The AI text is saved on the submission and reused until the scores change.
import copy
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from fastapi import HTTPException

from app.bfsi import store as bfsi_store
from app.bfsi.options import INDUSTRIES
from app.bfsi.scoring import bfsi_overall, is_kpi_scored
from app.core.db import get_db
from app.esg import scoring
from app.esg.submissions import esg_submissions_collection

logger = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parent / "templates" / "rating_summary_template.docx"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PILLARS = (("E", "Environment"), ("S", "Social"), ("G", "Governance"))
PREFIX = {"E": "environmental", "S": "social", "G": "governance"}
KPI_ROWS_PER_PILLAR = 4  # 2 strongest + 2 largest gaps -> 12 rows

NOT_AVAILABLE = ("The rating summary needs a KPI-scored report. Re-run the analysis with "
                 "\"Use cached result\" unticked, then download it again.")


# --- 1. Scores and facts from the stored report -------------------------------------------

def _grade(score: float) -> tuple[str, str]:
    return scoring.evaluate_score(score)


def _fmt(n) -> str:
    try:
        return f"{float(n):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def _date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value[:10]).strftime("%d %b %Y")
        except ValueError:
            return value
    return "—"


def _indian_fy(d) -> str:
    """"FY 2025-26" for a date (Indian financial year, 1 Apr - 31 Mar)."""
    if not isinstance(d, datetime):
        return "—"
    start = d.year if d.month >= 4 else d.year - 1
    return f"FY {start}-{(start + 1) % 100:02d}"


def _themes_lookup(pillar: str) -> dict:
    """{metric name: sub pillar} for a pillar, from esg_kpis."""
    return {d["metric"]: d["sub_pillar"] for d in get_db()[scoring.KPI_COLLECTION].find(
        {"pillar": pillar}, {"_id": 0, "metric": 1, "sub_pillar": 1})}


def _level_text(score: float) -> str:
    # The performance bands of app/esg/scoring.py SCORE_GUIDE, named.
    if score >= 81:
        return "Strong: targets met, measured improvement or assurance"
    if score >= 61:
        return "Good: measured results or progress against a target"
    if score >= 41:
        return "Moderate: real action, no measured result"
    if score >= 21:
        return "Weak: early or partial action, no result"
    if score > 0:
        return "Poor: penalties, incidents or a worsening trend"
    return "Not found in the report"


def _status(score: float) -> str:
    return {"strong": "Strong", "partial": "Partial"}.get(scoring.level(score), "Not found")


def _pages(pages) -> str:
    pages = [str(p) for p in pages or []]
    if not pages:
        return "Not found in the report"
    shown = ", ".join(pages[:6]) + (f" +{len(pages) - 6} more" if len(pages) > 6 else "")
    return f"Found on p. {shown}"


def build_facts(kind: str, doc: dict) -> dict:
    """Everything the summary shows that comes straight from the stored report."""
    if kind == "esg":
        final = doc.get("final") or {}
        coverage = final.get("kpi_coverage")
        scores = {c: float(final.get(f"{PREFIX[c]}_score") or 0) for c, _ in PILLARS}
        overall = float(final.get("composite_score") or 0)
        weights = scoring.weights_for(final)
        weights_text = scoring.weights_label(weights)
        company = doc.get("company_name") or "—"
        identifier = "Not provided"
        sector = final.get("sector") or "—"
        period = doc.get("report_year") or "—"
        assessed = doc.get("analyzed_at") or final.get("report_date")
        year = doc.get("year_score") or {}
        diff = year.get("difference")
        movement = (f"{float(diff):+.2f} vs {year.get('previous_year')}"
                    if isinstance(diff, (int, float)) else "First assessment")
        reasons = [{"category": c, "page": row.get("page"), "reason": (row.get(c) or {}).get("reason", "")}
                   for row in final.get("page_scores") or [] for c in ("Environment", "Social", "Governance")
                   if (row.get(c) or {}).get("reason")]
        extra = {}
        subtitle_extra = ""
    else:
        ai = doc.get("ai_analysis") or {}
        coverage = ai.get("kpi_coverage")
        scores = {"E": float(doc.get("e_score") or 0), "S": float(doc.get("s_score") or 0),
                  "G": float(doc.get("g_score") or 0)}
        ov = bfsi_overall(scores["E"], scores["S"], scores["G"], doc.get("loan_type", ""), is_kpi_scored(ai))
        overall = float(ov["overall"])
        weights_text = f"{ov['weightage_row']}: " + scoring.weights_label(
            {"Environment": ov["weights"]["e"], "Social": ov["weights"]["s"], "Governance": ov["weights"]["g"]})
        company = doc.get("borrower_name") or "—"
        identifier = doc.get("cin_gstin") or "Not provided"
        industry = INDUSTRIES.get(doc.get("industry", ""), {}).get("label", doc.get("industry", ""))
        sector = " · ".join(x for x in (industry, doc.get("sub_sector")) if x) or "—"
        created = doc.get("created_at")
        period = _indian_fy(created)
        assessed = doc.get("analysis_started_at") or created
        prev = bfsi_store.previous_scored(doc.get("cin_gstin", ""), doc["_id"])
        movement = (f"{overall - float(prev['overall_score']):+.2f} vs previous assessment"
                    if prev and isinstance(prev.get("overall_score"), (int, float)) else "First assessment")
        reasons = [{"category": dict(PILLARS)[c], "page": r.get("page"), "reason": r.get("reason", "")}
                   for c, _ in PILLARS for r in (ai.get("reasons") or {}).get(c) or [] if isinstance(r, dict)]
        extra = {k: ai.get(k) for k in ("top_risks", "top_improvements", "climate_risk", "governance_summary")}
        subtitle_extra = f" | BFSI borrower assessment ({doc.get('loan_type', '—')} loan)"

    if not isinstance(coverage, dict) or not any((coverage.get(n) or {}).get("kpis") for _, n in PILLARS):
        raise HTTPException(409, NOT_AVAILABLE)

    pillars, kpi_rows, total, found, specific = {}, [], 0, 0, 0
    for code, name in PILLARS:
        rows = [{"key": r["kpi"], "kpi": r["kpi"].strip().rstrip("."), "score": scoring.kpi_best(r),
                 "pages": r.get("pages") or [], "evidence": r.get("evidence") or {}}
                for r in (coverage.get(name) or {}).get("kpis") or []]
        lookup = _themes_lookup(code)
        themes = {}
        for r in rows:
            r["theme"] = lookup.get(r["key"])
            if r["theme"]:
                themes.setdefault(r["theme"], []).append(r)
        theme_list = []
        for theme, items in themes.items():
            avg = sum(i["score"] for i in items) / len(items)
            hits = sorted((i for i in items if i["score"] > 0), key=lambda i: -i["score"])
            theme_list.append({
                "name": theme, "score": round(avg, 2), "label": _grade(avg)[1], "hits": len(hits),
                "drivers": "; ".join(f"{i['kpi']} ({_fmt(i['score'])})" for i in hits[:3]) or "No KPI evidence found",
                "note": f"{len(hits)} of {len(items)} KPIs found in the report",
            })
        strong = sorted((r for r in rows if r["score"] > 0), key=lambda r: -r["score"])
        weakest_themes = [t["name"] for t in sorted(theme_list, key=lambda t: t["score"])]
        # Which missing KPIs the written summary names: the report's own evidence decides.
        # A theme the report discloses something in comes first -- those gaps are the ones
        # this company can actually close. A theme with no evidence at all comes last, so
        # the summary talks about what the report is about instead of listing metrics from
        # a line of business it is not in. Every missing KPI still scores 0 and still
        # counts towards the pillar; this is only the order they are talked about in.
        evidenced = {t["name"] for t in theme_list if t["hits"]}
        gaps = sorted((r for r in rows if r["score"] == 0),
                      key=lambda r: (0 if r["theme"] in evidenced else 1,
                                     weakest_themes.index(r["theme"]) if r["theme"] in weakest_themes else 99))
        grade, label = _grade(scores[code])
        detail = coverage.get(name) or {}
        pillars[code] = {
            "name": name, "score": scores[code], "grade": grade, "label": label, "themes": theme_list,
            "kpi_total": float(detail.get("score") or 0), "manual": detail.get("analyst_score") is not None,
            "strong": strong[:5], "gaps": gaps[:5], "kpi_count": len(rows),
        }
        for r in strong[:KPI_ROWS_PER_PILLAR // 2] + gaps[:KPI_ROWS_PER_PILLAR // 2]:
            kpi_rows.append({**r, "pillar": name})
        total += len(rows)
        found += len(strong)
        specific += sum(1 for r in strong if r["score"] >= 81)

    completeness = found / total * 100 if total else 0.0
    specificity = specific / found * 100 if found else 0.0
    grade, label = _grade(overall)
    return {
        "kind": kind, "company": company, "identifier": identifier, "sector": sector, "period": period,
        "assessed": _date(assessed), "status": "Final (analyst-edited)" if doc.get("report_edits") else "Final",
        "overall": overall, "grade": grade, "label": label, "weights": weights_text, "movement": movement,
        "pillars": pillars, "kpi_rows": kpi_rows,
        "completeness": {"pct": completeness, "found": found, "total": total,
                         "result": _band(completeness, 60, 30)},
        "specificity": {"pct": specificity, "specific": specific, "found": found,
                        "result": _band(specificity, 50, 20)},
        "reasons": reasons, "extra": extra, "subtitle_extra": subtitle_extra,
    }


def _band(pct: float, high: float, moderate: float) -> str:
    return "High" if pct >= high else "Moderate" if pct >= moderate else "Low"


# --- 2. Narrative (one AI call, cached on the submission) ---------------------------------

NARRATIVE_SYSTEM = (
    "You are an ESG rating analyst writing the text of an ESG Rating Summary. Use ONLY the scores, "
    "KPIs, themes and page reasons given. Do not invent disclosures, figures, targets, incidents or "
    "commitments. Every strength, weakness and priority must name a KPI or theme from the data. "
    "A missing KPI means the report does not disclose it, not that the company does not do it: "
    "describe gaps as missing or limited disclosure (e.g. 'No disclosure on biodiversity'). "
    "Build the text from what this report evidenced -- the KPIs found, their scores and their "
    "themes -- and name missing KPIs only as absent disclosure, in the order given. Priorities "
    "must stay inside the themes this report already covers; never recommend work in an area "
    "the report shows no involvement in, and never introduce a KPI, theme or topic that is not "
    "in the data. "
    "Plain professional English, short sentences. Respond in JSON with exactly these fields: "
    '{"executive_summary": "<3-4 sentences>", "key_rating_drivers": "<1 sentence>", '
    '"disclosure_headline": "<1 sentence on how complete and specific the evidence is>", '
    '"pillar_narratives": {"E": "<2-3 sentences>", "S": "<2-3 sentences>", "G": "<2-3 sentences>"}, '
    '"strengths": [<5 short strings>], "weaknesses": [<5 short strings>], '
    '"priorities": [{"area": "<pillar - theme>", "gap": "", "why": "", "action": ""}, <5 items>], '
    '"rating_rationale": "<3-4 sentences linking drivers to the final score>", '
    '"rating_interpretation": "<2 sentences on how to read this grade>"}'
)


def _kpi_evidence(r: dict) -> str:
    """"Water withdrawn (90) -- p.12: 22% reduction against a 2030 target": the KPI, the
    score it earned and the reason the scoring call gave for it (app/esg/scoring.py
    kpi_reasons), so the summary text is written from the evidence, not from the number
    alone."""
    text = f"{r['kpi']} ({_fmt(r['score'])})"
    ev = r.get("evidence") or {}
    if ev.get("reason"):
        page = f"p.{ev['page']}" if ev.get("page") is not None else "the report"
        return f"{text} -- {page}: {str(ev['reason'])[:200]}"
    return text


def _narrative_input(f: dict) -> dict:
    return {
        "calculator": "BFSI borrower assessment" if f["kind"] == "bfsi" else "ESG rating",
        "company": f["company"], "sector": f["sector"],
        "overall": {"score": round(f["overall"], 2), "grade": f["grade"], "label": f["label"], "weights": f["weights"]},
        "pillars": {c: {"name": p["name"], "score": round(p["score"], 2), "grade": p["grade"],
                        **({"set_by_analyst": True, "kpi_total": round(p["kpi_total"], 2)} if p["manual"] else {}),
                        "themes": [{"name": t["name"], "score": t["score"], "found": t["note"]} for t in p["themes"]],
                        "strongest_kpis": [_kpi_evidence(r) for r in p["strong"]],
                        "kpis_not_disclosed": [f"{r['kpi']} ({r['theme']})" if r["theme"] else r["kpi"]
                                               for r in p["gaps"]]}
                    for c, p in f["pillars"].items()},
        "evidence": {"kpis_found": f["completeness"]["found"], "kpis_total": f["completeness"]["total"],
                     "found_with_measured_data": f["specificity"]["specific"]},
        "page_reasons": [f"{r['category']} p.{r['page']}: {str(r['reason'])[:280]}" for r in f["reasons"][:30]],
        **({"analyst_notes": f["extra"]} if f["extra"] else {}),
    }


def _ask_ai(kind: str, system: str, user: str) -> dict:
    if kind == "bfsi":
        from app.bfsi.openai_client import get_client
        return get_client().json(user, system)
    from app.esg import llm as llm_mod
    raw = llm_mod.get_llm().generate_score(system + "\n\n" + user)
    return json.loads(raw)


def _collection(kind: str):
    return esg_submissions_collection() if kind == "esg" else bfsi_store.submissions_collection()


def narrative(kind: str, doc: dict, facts: dict) -> dict:
    payload = _narrative_input(facts)
    user = "Rating data (JSON):\n" + json.dumps(payload, ensure_ascii=False)
    fingerprint = hashlib.sha256(user.encode()).hexdigest()
    cached = doc.get("summary_ai") or {}
    if cached.get("fingerprint") == fingerprint and isinstance(cached.get("text"), dict):
        return cached["text"]
    try:
        text = _ask_ai(kind, NARRATIVE_SYSTEM, user)
        if not isinstance(text, dict) or not text.get("executive_summary"):
            raise ValueError("incomplete answer")
    except Exception as e:
        logger.error("rating summary narrative failed: %s", e)
        raise HTTPException(502, "Couldn't write the rating summary text right now. Try again in a minute.")
    _collection(kind).update_one({"_id": doc["_id"]}, {"$set": {"summary_ai": {
        "fingerprint": fingerprint, "text": text, "generated_at": datetime.now(timezone.utc)}}})
    return text


# --- 3. The Word document --------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def _set_paragraph(p: Paragraph, text: str) -> None:
    if p.runs:
        p.runs[0].text = text
        for r in p.runs[1:]:
            r.text = ""
    else:
        p.add_run(text)


def _replace_all(document, values: dict) -> None:
    def fix(p):
        if "{{" in p.text:
            _set_paragraph(p, _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "—")), p.text))
    for p in document.paragraphs:
        fix(p)
    for t in document.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    fix(p)
    for p in document.sections[0].footer.paragraphs:
        fix(p)


def _fill_rows(table: Table, rows: list[list[str]]) -> None:
    """Replace the table's data rows (all rows after the header) with rows, cloned from
    the first data row so the styling is kept."""
    proto = copy.deepcopy(table.rows[1]._tr)
    for r in list(table.rows)[1:]:
        table._tbl.remove(r._tr)
    for values in rows:
        tr = copy.deepcopy(proto)
        table._tbl.append(tr)
        row = table.rows[-1]
        for cell, value in zip(row.cells, values):
            _set_paragraph(cell.paragraphs[0], str(value))
            for extra in cell.paragraphs[1:]:
                extra._p.getparent().remove(extra._p)


def _remove(elements) -> None:
    for el in elements:
        el.getparent().remove(el)


def _drop_column(table: Table, index: int, give_width_to: int) -> None:
    grid = table._tbl.tblGrid
    cols = grid.findall(docx.oxml.ns.qn("w:gridCol"))
    width = int(cols[index].get(docx.oxml.ns.qn("w:w")))
    target = cols[give_width_to]
    target.set(docx.oxml.ns.qn("w:w"), str(int(target.get(docx.oxml.ns.qn("w:w"))) + width))
    grid.remove(cols[index])
    for tr in table._tbl.tr_lst:
        tcs = tr.tc_lst
        w = tcs[index].tcPr.tcW if tcs[index].tcPr is not None else None
        tcw = tcs[give_width_to].tcPr.tcW if tcs[give_width_to].tcPr is not None else None
        if w is not None and tcw is not None and w.w is not None and tcw.w is not None:
            tcw.w = tcw.w + w.w
        tr.remove(tcs[index])


def _numbered(cell, items: list[str]) -> None:
    """A cell with a bold heading paragraph then "1. {{X}}" paragraphs."""
    paragraphs = cell.paragraphs[1:]
    for i, p in enumerate(paragraphs):
        if i < len(items):
            _set_paragraph(p, f"{i + 1}. {items[i]}")
        else:
            p._p.getparent().remove(p._p)


def _list(items, n: int) -> list[str]:
    items = [str(x).strip() for x in (items or []) if str(x).strip()]
    return items[:n]


def render(facts: dict, text: dict) -> bytes:
    d = docx.Document(str(TEMPLATE))
    body = list(d.element.body.iterchildren())
    paras = {id(el): Paragraph(el, d) for el in body if el.tag.endswith("}p")}
    tables = [Table(el, d) for el in body if el.tag.endswith("}tbl")]

    def para_el(startswith: str):
        return next(el for el in body if id(el) in paras and paras[id(el)].text.startswith(startswith))

    # Removed: Transition & Controversy (from the blank line and header line before its
    # title), the developer appendix (from its page break; the section properties stay),
    # and the template's notes to developers.
    start = body.index(para_el("5. Forward-Looking")) - 2
    end = body.index(para_el("Rule: omit the controversy"))
    _remove(body[start:end + 1])
    _remove(body[body.index(para_el("Appendix A.")) - 2: len(body) - 1])
    _remove([para_el("Automation rule:"), para_el("Optional output:")])
    _set_paragraph(paras[id(para_el("6. Rating Interpretation"))], "5. Rating Interpretation & Methodology Notes")

    # Indian terms and wording that matches the KPI method.
    snapshot, tiles, exec_box, scorecard, e_themes, s_themes, g_themes, how_to_read, kpi_table, \
        strengths_box, priorities_table, rationale_box, method_box, quality_table, overall_quality, \
        scale_table, scope_box = [t for t in tables if t._tbl.getparent() is not None]
    _set_paragraph(snapshot.rows[1].cells[0].paragraphs[0], "CIN / GSTIN")
    _set_paragraph(paras[id(para_el("Applicable KPIs are selected"))],
                   "The strongest scores and the largest gaps in each pillar")
    _set_paragraph(how_to_read.rows[0].cells[0].paragraphs[1],
                   "Each page is placed in a pillar first, then each KPI it addresses is scored 0–100 on how "
                   "good the performance is: 0 when it is only mentioned, promised or too vague to judge, "
                   "1–20 poor (penalties, incidents, a worsening trend), 21–40 weak, 41–60 real action without "
                   "results, 61–80 measured results, 81–100 targets met or independently assured. A KPI keeps "
                   "its best score from any page, held down to 20 if any page showed poor performance; KPIs "
                   "not found in the report score 0.")
    _set_paragraph(paras[id(para_el("Evidence quality is reported"))],
                   "How much of the KPI library the report covers, and how specific its evidence is")
    _set_paragraph(method_box.rows[0].cells[0].paragraphs[1],
                   "Scores are based on the evidence in the report. Completeness shows how many KPIs the report "
                   "addresses; specificity shows how many of those are backed by measured data or targets with "
                   "progress. Both explain the score; neither is scored separately.")
    if facts["subtitle_extra"]:
        sub = paras[id(para_el("Automated companion report"))]
        _set_paragraph(sub, sub.text.replace(" | {{VERSION_DATE}}", facts["subtitle_extra"] + " | {{VERSION_DATE}}"))

    # Dynamic tables.
    for code, theme_table in (("E", e_themes), ("S", s_themes), ("G", g_themes)):
        themes = facts["pillars"][code]["themes"]
        _fill_rows(theme_table, [[t["name"], _fmt(t["score"]), t["label"], t["drivers"], t["note"]] for t in themes]
                   or [["Themes not available", "—", "—", "KPIs are not mapped to sub-pillars for this report", "—"]])
    _drop_column(kpi_table, 4, give_width_to=5)
    _fill_rows(kpi_table, [[r["pillar"], r["theme"] or "—", r["kpi"], _fmt(r["score"]), _pages(r["pages"]),
                            _status(r["score"]), _level_text(r["score"])] for r in facts["kpi_rows"]])
    _numbered(strengths_box.rows[0].cells[0], _list(text.get("strengths"), 5))
    _numbered(strengths_box.rows[0].cells[1], _list(text.get("weaknesses"), 5))
    priorities = [p for p in text.get("priorities") or [] if isinstance(p, dict)][:5]
    _fill_rows(priorities_table, [[str(i), p.get("area", ""), p.get("gap", ""), p.get("why", ""), p.get("action", "")]
                                  for i, p in enumerate(priorities, 1)])
    for row in list(quality_table.rows)[3:]:
        quality_table._tbl.remove(row._tr)  # consistency, verification, timeliness
    _set_paragraph(quality_table.rows[1].cells[1].paragraphs[0], "Share of the KPI library the report addresses")
    _set_paragraph(quality_table.rows[2].cells[1].paragraphs[0],
                   "Share of the KPIs found that are backed by measured data or targets with progress")
    _set_paragraph(overall_quality.rows[1].cells[0].paragraphs[0], "Overall data completeness")

    p, c, s = facts["pillars"], facts["completeness"], facts["specificity"]
    top_strengths = [f"{r['kpi']} ({_fmt(r['score'])})" for code in "ESG" for r in p[code]["strong"][:1]]
    # Weakest sub-pillars; reports scored before the metrics sheet have none, so their
    # biggest undisclosed KPIs stand in.
    weak_areas = [f"{t['name']} ({_fmt(t['score'])})" for t in
                  sorted((t for code in "ESG" for t in p[code]["themes"]), key=lambda t: t["score"])[:3]] \
        or [r["kpi"] for code in "ESG" for r in p[code]["gaps"]][:3]
    implication = {
        "High": "The report covers most of the KPI library, so the scores rest on broad evidence.",
        "Moderate": "The report covers part of the KPI library; missing KPIs hold the scores down.",
        "Low": "The report covers a small part of the KPI library; most KPIs score 0 for lack of evidence.",
    }[c["result"]]
    values = {
        "VERSION_DATE": "Generated " + datetime.now(timezone.utc).strftime("%d %b %Y"),
        "COMPANY_NAME": facts["company"], "SECTOR": facts["sector"], "CSE_IDENTIFIER": facts["identifier"],
        "REPORTING_PERIOD": facts["period"], "ASSESSMENT_DATE": facts["assessed"], "RATING_STATUS": facts["status"],
        "OVERALL_SCORE": _fmt(facts["overall"]), "RATING_GRADE": facts["grade"], "RATING_DEFINITION": facts["label"],
        "SCORE_MOVEMENT": facts["movement"], "OVERALL_DATA_COMPLETENESS": f"{c['result']} ({c['pct']:.0f}%)",
        "EXECUTIVE_RATING_SUMMARY": text.get("executive_summary", ""),
        "DISCLOSURE_HEADLINE": str(text.get("disclosure_headline", "")).rstrip("."),
        "KEY_RATING_DRIVERS": str(text.get("key_rating_drivers", "")).rstrip("."),
        "RATING_RATIONALE": text.get("rating_rationale", ""),
        "RATING_INTERPRETATION": f"{text.get('rating_interpretation', '')} Overall score weights: {facts['weights']}.",
        "DATA_COMPLETENESS_RESULT": f"{c['result']} ({c['pct']:.0f}%)",
        "DATA_COMPLETENESS_NOTE": f"{c['found']} of {c['total']} KPIs found in the report",
        "DATA_SPECIFICITY_RESULT": f"{s['result']} ({s['pct']:.0f}%)",
        "DATA_SPECIFICITY_NOTE": f"{s['specific']} of {s['found']} KPIs found are scored 81–100",
        "DATA_COMPLETENESS_IMPLICATION": implication,
    }
    for i in range(3):
        values[f"TOP_STRENGTH_{i + 1}"] = top_strengths[i] if i < len(top_strengths) else "—"
        values[f"TOP_WEAKNESS_{i + 1}"] = weak_areas[i] if i < len(weak_areas) else "—"
    for code in "ESG":
        values[f"{code}_SCORE"] = _fmt(p[code]["score"])
        values[f"{code}_GRADE"] = p[code]["grade"]
        values[f"{code}_LABEL"] = p[code]["label"]
        values[f"{code}_PILLAR_NARRATIVE"] = (text.get("pillar_narratives") or {}).get(code, "")
        values[f"{code}_STRONG_DRIVERS"] = "; ".join(f"{r['kpi']} ({_fmt(r['score'])})" for r in p[code]["strong"][:3]) or "No KPI evidence found"
        values[f"{code}_GAPS"] = "; ".join(r["kpi"] for r in p[code]["gaps"][:3]) or "No KPI gaps"
    _replace_all(d, values)
    # Scorecard: a pillar set by an analyst says so, with the KPI total beside it.
    for row, code in zip(list(scorecard.rows)[1:4], "ESG"):
        if p[code]["manual"]:
            _set_paragraph(row.cells[3].paragraphs[0],
                           f"{p[code]['label']} (set by analyst; KPI total {_fmt(p[code]['kpi_total'])})")

    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def available(kind: str, doc: dict) -> bool:
    """Whether a summary can be built: the report has a KPI Assessment."""
    holder = (doc.get("final") if kind == "esg" else doc.get("ai_analysis")) or {}
    coverage = holder.get("kpi_coverage")
    return isinstance(coverage, dict) and any((coverage.get(n) or {}).get("kpis") for _, n in PILLARS)


def build_summary(kind: str, doc: dict) -> bytes:
    facts = build_facts(kind, doc)
    return render(facts, narrative(kind, doc, facts))


def filename(kind: str, doc: dict) -> str:
    name = doc.get("company_name") if kind == "esg" else doc.get("borrower_name")
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "company").lower()).strip("-")[:40] or "company"
    return f"{kind}-rating-summary-{slug}-{str(doc['_id'])[:6]}.docx"
