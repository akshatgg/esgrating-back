def _seed(db, **overrides):
    doc = {
        "s_no": 601,
        "company_name": "Edelweiss Financial Services Limited",
        "sector": "Holding Company",
        "esg_rating": 60.0,
        "date_of_rating": "2025-10-15",
        "grade": "C",
        "category": "Average",
    }
    doc.update(overrides)
    db.esg_ratings.insert_one(dict(doc))
    return doc


# --- public list -----------------------------------------------------------------------

def test_public_ratings_empty(client):
    resp = client.get("/api/ratings")
    assert resp.status_code == 200
    assert resp.json() == []


def test_public_ratings_sorted_desc_and_formatted(client, db):
    _seed(db, s_no=601)
    _seed(db, s_no=602, company_name="Eicher Motors Limited", esg_rating=68.0)
    resp = client.get("/api/ratings")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["s_no"] for r in rows] == [602, 601]
    assert rows[0]["date_of_rating"] == "15-10-2025"
    assert rows[0]["esg_rating"] == 68.0
    assert isinstance(rows[0]["esg_rating"], float)


# --- admin list / search ----------------------------------------------------------------

def test_admin_ratings_requires_auth(client):
    assert client.get("/api/admin/ratings").status_code == 401


def test_admin_ratings_search_case_insensitive_over_six_fields(admin_client, db):
    _seed(db, s_no=601, company_name="Edelweiss Financial Services Limited", sector="Holding Company", esg_rating=60.0)
    _seed(db, s_no=602, company_name="Eicher Motors Limited", sector="Two/Three Wheelers", esg_rating=68.0)

    resp = admin_client.get("/api/admin/ratings", params={"search": "wheelers"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["s_no"] == 602

    # rating matched as a string
    resp2 = admin_client.get("/api/admin/ratings", params={"search": "60.0"})
    assert resp2.json()["total"] == 1
    assert resp2.json()["items"][0]["s_no"] == 601


def test_admin_ratings_pagination_50_per_page(admin_client, db):
    for i in range(60):
        _seed(db, s_no=601 + i, company_name=f"Company {i}")
    resp = admin_client.get("/api/admin/ratings", params={"page": 1})
    body = resp.json()
    assert len(body["items"]) == 50
    assert body["total"] == 60
    assert body["pages"] == 2

    resp2 = admin_client.get("/api/admin/ratings", params={"page": 2})
    assert len(resp2.json()["items"]) == 10


# --- admin CRUD ---------------------------------------------------------------------------

def test_admin_create_assigns_max_plus_one(admin_client, db):
    _seed(db, s_no=601)
    _seed(db, s_no=650)
    resp = admin_client.post("/api/admin/ratings", json={
        "company_name": "New Co", "sector": "Banking", "esg_rating": 70.5,
        "date_of_rating": "2026-01-01", "grade": "B+", "category": "Very Good",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["s_no"] == 651
    assert db.esg_ratings.find_one({"s_no": 651})["company_name"] == "New Co"


def test_admin_create_first_row_is_s_no_1(admin_client, db):
    resp = admin_client.post("/api/admin/ratings", json={
        "company_name": "First Co", "sector": "Banking", "esg_rating": 70.5,
        "date_of_rating": "2026-01-01", "grade": "B+", "category": "Very Good",
    })
    assert resp.json()["s_no"] == 1


def test_admin_update(admin_client, db):
    _seed(db, s_no=601)
    resp = admin_client.put("/api/admin/ratings/601", json={
        "company_name": "Renamed Co", "sector": "Holding Company", "esg_rating": 65.0,
        "date_of_rating": "2025-10-15", "grade": "B", "category": "Good",
    })
    assert resp.status_code == 200
    assert db.esg_ratings.find_one({"s_no": 601})["company_name"] == "Renamed Co"


def test_admin_update_missing_404(admin_client, db):
    resp = admin_client.put("/api/admin/ratings/9999", json={
        "company_name": "X", "sector": "X", "esg_rating": 1.0,
        "date_of_rating": "2025-10-15", "grade": "C", "category": "Average",
    })
    assert resp.status_code == 404


def test_admin_delete(admin_client, db):
    _seed(db, s_no=601)
    resp = admin_client.delete("/api/admin/ratings/601")
    assert resp.status_code == 200
    assert db.esg_ratings.find_one({"s_no": 601}) is None


def test_admin_delete_missing_404(admin_client):
    resp = admin_client.delete("/api/admin/ratings/9999")
    assert resp.status_code == 404


# --- import ------------------------------------------------------------------------------

def test_import_bad_header_422(admin_client):
    csv_text = "Wrong,Header,Row\nA,B,C\n"
    resp = admin_client.post(
        "/api/admin/ratings/import",
        files={"file": ("ratings.csv", csv_text, "text/csv")},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "CSV header row does not match the expected format."


def test_import_success_and_skips(admin_client, db):
    csv_text = (
        "Company_Name,Sector,ESG_Rating,Date_of_Rating,Grade,Category\n"
        "Acme Ltd,Banking,72.5,2025-10-15,B+,Very Good\n"
        ",Banking,50,2025-10-15,C,Average\n"  # blank company name -> skipped
        "TooFew,OnlyThree\n"  # fewer than 6 columns -> skipped
        "Beta Ltd,Insurance,80,2025-11-01,A,Excellent\n"
    )
    resp = admin_client.post(
        "/api/admin/ratings/import",
        files={"file": ("ratings.csv", csv_text, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json() == {"message": "Import complete: 2 row(s) inserted, 2 row(s) skipped."}
    assert db.esg_ratings.count_documents({}) == 2
    acme = db.esg_ratings.find_one({"company_name": "Acme Ltd"})
    assert acme["esg_rating"] == 72.5
    assert acme["date_of_rating"] == "2025-10-15"


def test_import_requires_auth(client):
    resp = client.post(
        "/api/admin/ratings/import",
        files={"file": ("ratings.csv", "a,b,c\n", "text/csv")},
    )
    assert resp.status_code == 401
