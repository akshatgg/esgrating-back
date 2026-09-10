# scripts/migrate_mysql.py
# One-shot, idempotent MySQL -> MongoDB migration for the two custom WordPress tables
# (docs/analysis/esg.md B2-B3):
#   ESG_RATING_2025_CUSTOM -> esg_ratings (upsert by s_no)
#   EST_ADMIN_2025_CUSTOM  -> admin_users (upsert by username, hash kept as-is)
# Also copies every file from the BFSI uploads dir into settings.upload_dir/bfsi/,
# skipping files that already exist there, so existing bfsi_submissions.file_path
# values keep resolving after the app switches to the new upload_dir.
#
# Usage: uv run python -m scripts.migrate_mysql [--dump PATH] [--bfsi-uploads PATH]
#
# The dump is streamed (never fully loaded into memory) with a small hand-written
# tokenizer that understands quoted strings (with \' and '' escapes), NULL, and
# numbers -- enough to parse the `INSERT INTO \`table\` VALUES (...),(...);` statements
# mysqldump produces, without pulling in a SQL parser dependency.
import argparse
import gzip
import re
import shutil
import sys
from pathlib import Path

from app.core.config import settings
from app.core.db import get_db

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DUMP = REPO_ROOT.parent / "esgratings" / "esgratings-db.sql.gz"
DEFAULT_BFSI_UPLOADS = REPO_ROOT.parent / "esgratings" / "site" / "bfsi-calculator" / "uploads"

RATING_TABLE = "ESG_RATING_2025_CUSTOM"
ADMIN_TABLE = "EST_ADMIN_2025_CUSTOM"

# Column order matches the CREATE TABLE statements in the dump.
RATING_COLUMNS = ("s_no", "company_name", "sector", "esg_rating", "date_of_rating", "grade", "category")
ADMIN_COLUMNS = ("id", "username", "password")

_PREFIXES = {
    RATING_TABLE: f"INSERT INTO `{RATING_TABLE}` VALUES",
    ADMIN_TABLE: f"INSERT INTO `{ADMIN_TABLE}` VALUES",
}

_INT_RE = re.compile(r"^[+-]?\d+$")


def tokenize_values(text: str) -> list[list]:
    """Parse a `(v,v,...),(v,v,...)` value list (no surrounding VALUES/semicolon) into a
    list of rows, each a list of python values: str for quoted strings, int/float for
    bare numbers, None for NULL. Handles `\\'`- and `''`-escaped quotes inside strings."""
    i, n = 0, len(text)
    rows: list[list] = []

    def skip_ws(pos: int) -> int:
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        return pos

    while True:
        i = skip_ws(i)
        while i < n and text[i] == ",":
            i = skip_ws(i + 1)
        if i >= n or text[i] != "(":
            break
        i += 1  # consume '('
        row: list = []
        while True:
            i = skip_ws(i)
            if i < n and text[i] == "'":
                i += 1
                buf = []
                while i < n:
                    c = text[i]
                    if c == "\\" and i + 1 < n:
                        buf.append(text[i + 1])
                        i += 2
                        continue
                    if c == "'":
                        if i + 1 < n and text[i + 1] == "'":
                            buf.append("'")
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                row.append("".join(buf))
            else:
                j = i
                while j < n and text[j] not in ",)":
                    j += 1
                token = text[i:j].strip()
                i = j
                if token == "NULL":
                    row.append(None)
                elif _INT_RE.match(token):
                    row.append(int(token))
                else:
                    try:
                        row.append(float(token))
                    except ValueError:
                        row.append(token)
            i = skip_ws(i)
            if i < n and text[i] == ",":
                i += 1
                continue
            if i < n and text[i] == ")":
                i += 1
            break
        rows.append(row)
        i = skip_ws(i)
        if i < n and text[i] == ",":
            i += 1
            continue
        break

    return rows


def _iter_insert_rows(fp):
    """Yields (table_name, row) for every value tuple in every INSERT INTO
    `table` VALUES ...; statement for a known table, streaming fp line by line."""
    buf = None
    table = None
    for line in fp:
        if buf is None:
            for t, prefix in _PREFIXES.items():
                if line.startswith(prefix):
                    table = t
                    buf = line[len(prefix):]
                    break
            else:
                continue
        else:
            buf += line
        stripped = buf.rstrip()
        if stripped.endswith(";"):
            values_text = stripped[:-1]
            for row in tokenize_values(values_text):
                yield table, row
            buf, table = None, None


def _open_dump(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "rt", encoding="utf-8", errors="replace")


def _rating_doc(row: list) -> tuple[int, dict]:
    values = dict(zip(RATING_COLUMNS, row))
    s_no = int(values.pop("s_no"))
    esg_rating = values.get("esg_rating")
    values["esg_rating"] = float(esg_rating) if esg_rating is not None else None
    date_val = values.get("date_of_rating")
    values["date_of_rating"] = str(date_val) if date_val is not None else None
    return s_no, values


def _admin_doc(row: list) -> tuple[str, dict]:
    values = dict(zip(ADMIN_COLUMNS, row))
    username = values["username"]
    return username, {"username": username, "password": values["password"]}


def migrate_ratings_and_admins(dump_path: Path) -> dict:
    db = get_db()
    ratings_col = db["esg_ratings"]
    admin_col = db["admin_users"]
    ratings_col.create_index("s_no", unique=True)

    ratings_upserted = 0
    admins_upserted = 0

    with _open_dump(dump_path) as fp:
        for table, row in _iter_insert_rows(fp):
            if table == RATING_TABLE:
                s_no, doc = _rating_doc(row)
                ratings_col.update_one({"s_no": s_no}, {"$set": {**doc, "s_no": s_no}}, upsert=True)
                ratings_upserted += 1
            elif table == ADMIN_TABLE:
                username, doc = _admin_doc(row)
                admin_col.update_one({"username": username}, {"$set": doc}, upsert=True)
                admins_upserted += 1

    return {"ratings": ratings_upserted, "admins": admins_upserted}


def copy_bfsi_uploads(uploads_path: Path) -> int:
    if not uploads_path.is_dir():
        return 0
    dest = (settings.upload_dir / "bfsi").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in uploads_path.iterdir():
        if not f.is_file():
            continue
        target = dest / f.name
        if target.exists():
            continue
        shutil.copy2(f, target)
        copied += 1
    return copied


def migrate(dump_path: Path, uploads_path: Path) -> dict:
    result = migrate_ratings_and_admins(dump_path)
    result["files_copied"] = copy_bfsi_uploads(uploads_path)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, default=DEFAULT_DUMP)
    parser.add_argument("--bfsi-uploads", type=Path, default=DEFAULT_BFSI_UPLOADS)
    args = parser.parse_args(argv)

    if not args.dump.exists():
        print(f"Dump not found: {args.dump}", file=sys.stderr)
        return 1

    result = migrate(args.dump, args.bfsi_uploads)
    print(f"esg_ratings: {result['ratings']} row(s) upserted")
    print(f"admin_users: {result['admins']} row(s) upserted")
    print(f"bfsi uploads: {result['files_copied']} file(s) copied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
