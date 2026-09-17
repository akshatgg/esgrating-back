# scripts/migrate_legacy_esg.py
# One-shot, idempotent import of the old ESG calculator's history (esg_score_calculator-
# master, the production Atlas database) into the new app's esg_submissions, so the admin
# lists every report that already exists.
#
# The old API kept no submissions of its own:
#   user_details  one row per email (insert_user upserts by email), file_names appended
#   esg_report    one row per completed analysis: page records, final result appended last
#   esg_hashes    the final result per report text (the cache), with company_id + filename
# The new admin reads only esg_submissions, so without this import none of it shows.
#
# One esg_submissions document per (user, uploaded file), carrying the final result when
# the old calculator produced one. company_id stays the user_details _id, so the page-
# scores export and the year-over-year comparison keep reading the original esg_report
# runs. The old collections are only read. Re-running skips files already imported
# (legacy.user_id + legacy.file).
#
# The uploaded PDFs never lived in MongoDB (they stayed on the old API host), so imported
# submissions have no file_path: the admin shows the report but cannot download the
# original or re-run the analysis until the file is supplied.
#
# Usage: uv run python -m scripts.migrate_legacy_esg [--dry-run]
import argparse

from app.core.db import get_db
from app.esg.store import get_esg_score


def _final_from_run(run: dict) -> dict | None:
    """The final report the pipeline appends as the last element of esg_report.analysis
    (page records carry a "category"; the final report does not)."""
    for rec in reversed(run.get("analysis") or []):
        if isinstance(rec, dict) and "composite_score" in rec and "category" not in rec:
            return rec
    return None


def _run_files(run: dict) -> list[str]:
    files = run.get("filename")
    if isinstance(files, list):
        return [str(f) for f in files]
    return [str(files)] if files else []


def build_submissions(db) -> list[dict]:
    """Every (user, file) the old calculator saw, as new-app submission documents."""
    runs = sorted(db.esg_report.find(), key=lambda r: r["_id"], reverse=True)       # newest first
    hashes = sorted(db.esg_hashes.find(), key=lambda h: h["_id"], reverse=True)
    docs = []
    for user in db.user_details.find():
        uid = user["_id"]
        user_runs = [r for r in runs if r.get("company_id") == uid]
        user_hashes = [h for h in hashes if h.get("company_id") == uid]
        # Every file the user uploaded, plus any file a run or cached result names.
        files = list(dict.fromkeys(
            [str(f) for f in (user.get("file_names") or [])]
            + [f for r in user_runs for f in _run_files(r)]
            + [str(h["filename"]) for h in user_hashes if h.get("filename")]
        ))
        for file in files:
            run = next((r for r in user_runs if file in _run_files(r)), None)
            cached = next((h for h in user_hashes if h.get("filename") == file), None)
            ai_final = _final_from_run(run) if run else None
            # The cached result is what the old admin showed and what its /update_report
            # edited (hand-adjusted round scores for some reports), so it is the official
            # result. The run's own final is the unedited AI version, kept for reference.
            final = (cached or {}).get("llm_response") or ai_final
            source_id = (run or cached or user)["_id"]
            created = source_id.generation_time
            doc = {
                "name": (user.get("fullname") or "").strip(),
                "email": (user.get("email") or "").strip(),
                "designation": "",
                "company_name": (user.get("company_name") or "").strip(),
                "mobile_number": str(user.get("mobile_number") or "").strip(),
                "report_year": (run or {}).get("report_year") or "",
                "file_path": None,
                "file_sha256": None,
                "original_filename": file,
                "submit_ip": "",
                "status": "report_generated" if final else "new",
                "analysis_status": "done" if final else "idle",
                "analysis_error": None,
                "created_at": created,
                "company_id": uid,
                "legacy": {
                    "source": "esg_score_calculator",
                    "user_id": uid,
                    "file": file,
                    "esg_report_id": run["_id"] if run else None,
                    "esg_hash_id": cached["_id"] if cached else None,
                },
            }
            if ai_final and ai_final != final:
                doc["legacy"]["ai_final"] = ai_final
            if final:
                doc["final"] = final
                doc["year_score"] = get_esg_score(uid)
                doc["analyzed_at"] = created
            docs.append(doc)
    return docs


def migrate(dry_run: bool = False) -> dict:
    db = get_db()
    imported, skipped = [], []
    for doc in build_submissions(db):
        key = {"legacy.user_id": doc["legacy"]["user_id"], "legacy.file": doc["legacy"]["file"]}
        label = f"{doc['company_name'] or '?'} -- {doc['original_filename'] or '(no file name)'}"
        if db.esg_submissions.find_one(key, {"_id": 1}):
            skipped.append(label)
            continue
        if not dry_run:
            db.esg_submissions.insert_one(doc)
        imported.append((
            label,
            doc.get("final", {}).get("composite_score"),
            bool(doc["legacy"]["esg_report_id"]),
            (doc["legacy"].get("ai_final") or {}).get("composite_score"),
        ))
    return {"imported": imported, "skipped": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show what would be imported; write nothing")
    args = parser.parse_args(argv)

    print(f"database: {get_db().name}{'  (dry run)' if args.dry_run else ''}")
    result = migrate(args.dry_run)
    for label, score, pages, ai_score in result["imported"]:
        report = f"composite {round(score, 2)}" if score is not None else "no report"
        edited = f" | edited in old admin (AI said {round(ai_score, 2)})" if ai_score is not None else ""
        print(f"  {'would import' if args.dry_run else 'imported'}: {label} | {report}"
              f"{' | page scores' if pages else ''}{edited}")
    for label in result["skipped"]:
        print(f"  already imported, skipped: {label}")
    print(f"esg_submissions: {len(result['imported'])} {'to import' if args.dry_run else 'imported'}, "
          f"{len(result['skipped'])} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
