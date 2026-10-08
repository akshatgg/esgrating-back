"""Put back the KPI evidence an edit stripped from a stored report, in place.

Saving any KPI score used to rescore the whole pillar as if every KPI in it had been edited,
which dropped every KPI's reason and page from the stored report (user, 2026-10-08). The
detailed report lost its Reason column for that pillar, and the rating text, rewritten from
those rows, cited no page at all.

report_original still holds the evidence as the analysis wrote it, so this recomputes each
report's saved edits from it with the fixed editor -- exactly what saving the same edits
again in the editor now does. The edits are the same, so the scores are the same: a report
whose pillar or overall score would come out different is reported and left alone. Running
it twice is the same as running it once.

usage:
    uv run python -m scripts.repair_kpi_evidence [--apply]

Without --apply it only reports.
"""
import sys

from app.bfsi import store as bfsi_store
from app.esg.submissions import esg_submissions_collection
from app.reports import editing

ESG_SCORES = ("environmental_score", "social_score", "governance_score", "composite_score")
BFSI_SCORES = ("e_score", "s_score", "g_score", "overall_score")


def _scores(kind: str, values: dict) -> tuple:
    if kind == "esg":
        return tuple(round(float(values.get(k) or 0), 4) for k in ESG_SCORES)
    return tuple(round(float(values.get(k) or 0), 4) for k in BFSI_SCORES)


def _missing_evidence(kind: str, values: dict) -> int:
    """KPI rows that scored but carry no evidence."""
    holder = values.get("final") if kind == "esg" else values.get("ai_analysis")
    coverage = (holder or {}).get("kpi_coverage") or {}
    return sum(1 for detail in coverage.values() if isinstance(detail, dict)
               for r in detail.get("kpis") or [] if r.get("score") and not r.get("evidence"))


def repair(kind: str, col, apply: bool) -> int:
    changed = 0
    for doc in col.find({"report_original": {"$exists": True}}):
        edits = editing.stored_edits(doc)
        if not any(edits["kpi_scores"].values()):
            continue
        stored = editing.compute(kind, doc, editing.load_context(kind, doc), edits)["stored"]
        current = {"final": doc.get("final")} if kind == "esg" else doc
        before, after = _missing_evidence(kind, current), _missing_evidence(kind, stored)
        if before == after:
            continue
        name = doc.get("company_name") or doc.get("borrower_name")
        old = _scores(kind, (doc.get("final") or {}) if kind == "esg" else doc)
        new = _scores(kind, stored["final"] if kind == "esg" else stored)
        if old != new:
            print(f"  SKIP {kind} {doc['_id']} {name}: scores would change {old} -> {new}")
            continue
        print(f"  {kind} {doc['_id']} {name}: KPIs without evidence {before} -> {after}")
        if apply:
            col.update_one({"_id": doc["_id"]}, {"$set": stored})
        changed += 1
    return changed


def main(argv: list[str]) -> None:
    apply = "--apply" in argv
    total = repair("esg", esg_submissions_collection(), apply) + \
        repair("bfsi", bfsi_store.submissions_collection(), apply)
    print(f"{total} report(s) {'repaired' if apply else 'to repair; run with --apply to write'}")


if __name__ == "__main__":
    main(sys.argv[1:])
