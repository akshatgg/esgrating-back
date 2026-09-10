import sys
from app.auth.service import hash_password
from app.core.db import get_db

if len(sys.argv) != 3:
    sys.exit("usage: python -m scripts.create_admin <username> <password>")
get_db().admin_users.update_one({"username": sys.argv[1]}, {"$set": {"password": hash_password(sys.argv[2])}}, upsert=True)
print(f"admin '{sys.argv[1]}' saved")
