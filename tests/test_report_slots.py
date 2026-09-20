# The report sheet's optional corner (text / logo / nothing) and its optional footer
# line (app/reports/editing.py, app/reports/router.py) -- user, 2026-09-20.
from datetime import datetime, timezone

import pytest
from bson import ObjectId

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


def _esg(db, **extra):
    return db.esg_submissions.insert_one({
        "company_name": "Acme Ltd", "email": "asha@example.com", "name": "Asha",
        "report_year": "2025-2026", "analyzed_at": datetime(2026, 9, 17, tzinfo=timezone.utc),
        "final": {"environmental_score": 50.0, "social_score": 60.0, "governance_score": 70.0,
                  "composite_score": 60.0},
        **extra}).inserted_id


def _edits(client, sid, fields):
    return client.put(f"/api/admin/esg/submissions/{sid}/report/edits", json={"fields": fields})


def _logo(client, sid, slot=None):
    q = f"?slot={slot}" if slot else ""
    return f"/api/admin/esg/submissions/{sid}/report/logo{q}"


# --- the corner: text (default), a logo, or nothing --------------------------------

def test_header_slot_none_is_stored(admin_client, db):
    """Blank text is "no override" everywhere, so hiding the corner needs a real value."""
    sid = _esg(db)
    resp = _edits(admin_client, sid, {"header_slot": "none"})
    assert resp.status_code == 200
    assert resp.json()["edits"]["fields"]["header_slot"] == "none"
    assert db.esg_submissions.find_one({"_id": sid})["report_edits"]["fields"]["header_slot"] == "none"


def test_header_slot_default_is_not_stored(admin_client, db):
    sid = _esg(db)
    assert "header_slot" not in _edits(admin_client, sid, {"header_slot": "text"}).json()["edits"]["fields"]


@pytest.mark.parametrize("bad", ["hidden", "TEXT", "none;", 3])
def test_header_slot_rejects_anything_else(admin_client, db, bad):
    sid = _esg(db)
    resp = _edits(admin_client, sid, {"header_slot": bad})
    assert resp.status_code == 422
    assert "header_slot" in resp.json()["detail"]


def test_header_slot_is_trimmed(admin_client, db):
    sid = _esg(db)
    assert _edits(admin_client, sid, {"header_slot": " logo "}).json()["edits"]["fields"]["header_slot"] == "logo"


def test_footer_note_round_trips_and_clears(admin_client, db):
    sid = _esg(db)
    note = "Prepared for internal circulation only."
    assert _edits(admin_client, sid, {"footer_note": note}).json()["edits"]["fields"]["footer_note"] == note
    # Blank puts it back to showing nothing -- the footer has no AI value to fall back to.
    assert "footer_note" not in _edits(admin_client, sid, {"footer_note": ""}).json()["edits"]["fields"]


# --- the corner logo ---------------------------------------------------------------

def test_corner_logo_is_its_own_slot(admin_client, db):
    sid = _esg(db)
    admin_client.post(_logo(admin_client, sid), files={"logo": ("a.png", PNG, "image/png")})
    resp = admin_client.post(_logo(admin_client, sid, "corner"), files={"logo": ("b.png", PNG, "image/png")})
    assert resp.status_code == 200

    edits = db.esg_submissions.find_one({"_id": sid})["report_edits"]
    assert edits["logo"] and edits["corner_logo"] and edits["logo"] != edits["corner_logo"]
    eff = resp.json()["effective"]
    assert eff["logo_url"].endswith("/report/logo")
    assert eff["corner_logo_url"].endswith("/report/logo?slot=corner")


def test_corner_logo_delete_leaves_the_main_logo(admin_client, db):
    sid = _esg(db)
    admin_client.post(_logo(admin_client, sid), files={"logo": ("a.png", PNG, "image/png")})
    admin_client.post(_logo(admin_client, sid, "corner"), files={"logo": ("b.png", PNG, "image/png")})

    resp = admin_client.delete(_logo(admin_client, sid, "corner"))
    assert resp.status_code == 200
    assert resp.json()["effective"]["corner_logo_url"] is None
    assert resp.json()["effective"]["logo_url"] is not None
    edits = db.esg_submissions.find_one({"_id": sid})["report_edits"]
    assert edits["logo"] and not edits.get("corner_logo")


def test_corner_logo_is_served_and_kept_apart(admin_client, db):
    sid = _esg(db)
    admin_client.post(_logo(admin_client, sid, "corner"), files={"logo": ("b.png", PNG, "image/png")})
    assert admin_client.get(_logo(admin_client, sid, "corner")).status_code == 200
    # Nothing in the main slot, so that one is still a 404.
    assert admin_client.get(_logo(admin_client, sid)).status_code == 404


def test_unknown_slot_is_rejected(admin_client, db):
    sid = _esg(db)
    assert admin_client.get(_logo(admin_client, sid, "sidebar")).status_code == 422


def test_a_corner_logo_alone_keeps_the_edits(admin_client, db):
    """The tidy-up drops a report_edits with no content and no logo; a corner logo counts."""
    sid = _esg(db)
    admin_client.post(_logo(admin_client, sid, "corner"), files={"logo": ("b.png", PNG, "image/png")})
    admin_client.put(f"/api/admin/esg/submissions/{sid}/report/edits", json={"fields": {}})
    assert db.esg_submissions.find_one({"_id": sid})["report_edits"].get("corner_logo")
