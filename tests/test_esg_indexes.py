"""The ESG Submissions list (GET /api/admin/esg/combined) sorts esg_submissions by
created_at then _id. Without an index on those two fields MongoDB sorts the documents in
memory, and once they passed its 32 MB limit every page load failed with "Sort exceeded
memory limit" (user, 2026-10-25). The API creates the index at startup."""
from fastapi.testclient import TestClient

from app import main
from app.esg import submissions


def _sort_keys(db):
    return [info["key"] for info in db.esg_submissions.index_information().values()]


def test_ensure_indexes_adds_the_list_sort_index(db):
    submissions.ensure_indexes()

    assert [("created_at", -1), ("_id", -1)] in _sort_keys(db)


def test_startup_creates_the_list_sort_index(db, upload_dir):
    with TestClient(main.app):
        pass

    assert [("created_at", -1), ("_id", -1)] in _sort_keys(db)
