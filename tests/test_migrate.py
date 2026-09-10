import gzip

from scripts.migrate_mysql import migrate, tokenize_values


def test_tokenizer_handles_quoted_comma_escaped_quote_and_null():
    text = (
        "(1,'Acme, Inc','O\\'Brien Sector',72.5,'2025-10-15',NULL,'Average'),"
        "(2,'It''s Fine Ltd','Banking',60,'2025-11-01','C','Average')"
    )
    rows = tokenize_values(text)
    assert rows == [
        [1, "Acme, Inc", "O'Brien Sector", 72.5, "2025-10-15", None, "Average"],
        [2, "It's Fine Ltd", "Banking", 60, "2025-11-01", "C", "Average"],
    ]


def _write_dump(path, ratings_sql, admin_sql):
    content = (
        "-- dummy dump\n"
        f"INSERT INTO `ESG_RATING_2025_CUSTOM` VALUES\n{ratings_sql};\n"
        f"INSERT INTO `EST_ADMIN_2025_CUSTOM` VALUES\n{admin_sql};\n"
    )
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(content)


def test_migrate_upserts_ratings_and_admins_idempotently(db, tmp_path, upload_dir):
    dump_path = tmp_path / "dump.sql.gz"
    ratings_sql = (
        "(601,'Edelweiss Financial Services Limited','Holding Company',60.0,'2025-10-15','C','Average'),\n"
        "(602,'Eicher Motors Limited','Two/Three Wheelers',68.0,'2025-10-15','B','Good')"
    )
    admin_sql = "(1,'admin','$2y$10$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ12')"
    _write_dump(dump_path, ratings_sql, admin_sql)

    uploads_dir = tmp_path / "bfsi_uploads"
    uploads_dir.mkdir()
    (uploads_dir / "report1.pdf").write_bytes(b"%PDF-1.4 fake")

    result1 = migrate(dump_path, uploads_dir)
    assert result1 == {"ratings": 2, "admins": 1, "files_copied": 1}
    assert db.esg_ratings.count_documents({}) == 2
    assert db.admin_users.count_documents({}) == 1
    row = db.esg_ratings.find_one({"s_no": 601})
    assert row["company_name"] == "Edelweiss Financial Services Limited"
    assert row["esg_rating"] == 60.0
    assert row["date_of_rating"] == "2025-10-15"
    admin = db.admin_users.find_one({"username": "admin"})
    assert admin["password"] == "$2y$10$abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ12"

    # running again is idempotent: same counts, no duplicates, file already exists so not re-copied
    result2 = migrate(dump_path, uploads_dir)
    assert result2 == {"ratings": 2, "admins": 1, "files_copied": 0}
    assert db.esg_ratings.count_documents({}) == 2
    assert db.admin_users.count_documents({}) == 1
