"""Add the Question to esg_kpis documents that predate it, in place.

scripts/import_esg_kpis.py --replace rebuilds the whole collection: it deletes every
document and inserts the workbook fresh. That is right for a first import, but on a live
system it leaves esg_kpis empty for as long as the insert takes, and an insert that fails
leaves it empty for good. Adding a field to documents that are otherwise already correct
does not need that risk.

So this matches each workbook row to its document on (pillar, metric) -- unique on both
sides -- and sets the Question on it, along with the sub pillar when the workbook has since
moved the metric. Nothing is deleted, no _id changes, and running it twice is the same as
running it once.

order is deliberately NOT written. It only numbers the KPI list inside one prompt (see
app/esg/scoring.py kpi_list_text) and stored reports key on the metric name, so a stale
order changes no result -- while rewriting it in place would collide with the unique
(pillar, order) index partway through. Differences are reported instead. To adopt a new
order, re-import the workbook with --replace when the collection can afford to be rebuilt.

usage:
    uv run --with openpyxl python -m scripts.backfill_kpi_questions <workbook.xlsx> [--apply]

Without --apply it only reports.
"""
import sys

from pymongo import UpdateOne

from app.core.db import get_db
from scripts.import_esg_kpis import COLLECTION, read_workbook

KEY = ("pillar", "metric")
# Written when the workbook disagrees. The unique index over these plus pillar is checked
# before writing, so a move can never collide with a metric already sitting in that slot.
TAXONOMY = ("sub_pillar", "sub_pillar_1", "sub_pillar_order")


def main(argv: list[str]) -> None:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit(__doc__)
    docs, _ = read_workbook(args[0])
    col = get_db()[COLLECTION]
    stored = {tuple(d[k] for k in KEY): d for d in col.find({})}
    print(f"{len(docs)} rows in the workbook, {len(stored)} documents in {COLLECTION}")

    missing = [tuple(d[k] for k in KEY) for d in docs if tuple(d[k] for k in KEY) not in stored]
    if missing:
        for key in missing[:5]:
            print("  no document for:", key)
        raise SystemExit(f"{len(missing)} workbook rows have no document; re-import instead")

    # Where each (pillar, sub_pillar, sub_pillar_1, metric) slot is taken now, so a move is
    # only written into a slot that is free.
    taken = {(d["pillar"], d["sub_pillar"], d["sub_pillar_1"], d["metric"]): k
             for k, d in stored.items()}

    writes, moved, order_drift, already = [], [], [], 0
    for d in docs:
        key = tuple(d[k] for k in KEY)
        cur = stored[key]
        if cur.get("order") != d["order"]:
            order_drift.append((d["pillar"], d["metric"], cur.get("order"), d["order"]))

        change = {}
        if cur.get("question") != d["question"]:
            change["question"] = d["question"]
            change["source.question_raw"] = d["source"]["question_raw"]
        if any(cur.get(f) != d[f] for f in TAXONOMY):
            slot = (d["pillar"], d["sub_pillar"], d["sub_pillar_1"], d["metric"])
            holder = taken.get(slot)
            if holder not in (None, key):
                raise SystemExit(f"{key} cannot move into {slot}: already held by {holder}")
            change.update({f: d[f] for f in TAXONOMY})
            moved.append((d["pillar"], d["metric"], cur.get("sub_pillar"), d["sub_pillar"]))
        if change:
            writes.append(UpdateOne({"_id": cur["_id"]}, {"$set": change}))
        else:
            already += 1

    print(f"already correct: {already}\nto update: {len(writes)}")
    print(f"sub pillar moves: {len(moved)}")
    for p, m, was, now in moved:
        print(f"  {p} {m!r}: {was!r} -> {now!r}")
    print(f"order differences (reported, not written): {len(order_drift)}")

    if "--apply" not in argv:
        print("report only: nothing written (pass --apply to write)")
        return
    if not writes:
        print("nothing to do")
        return

    result = col.bulk_write(writes, ordered=False)
    print(f"updated {result.modified_count} documents")
    scorable = col.count_documents({"is_meta": {"$ne": True}})
    with_q = col.count_documents({"is_meta": {"$ne": True}, "question": {"$exists": True, "$ne": ""}})
    print(f"scorable metrics with a question: {with_q}/{scorable}")


if __name__ == "__main__":
    main(sys.argv[1:])
