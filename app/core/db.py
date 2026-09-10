# app/core/db.py
from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings

_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000)
        _db = client[settings.mongo_db]
    return _db


def set_db(db) -> None:
    global _db
    _db = db
