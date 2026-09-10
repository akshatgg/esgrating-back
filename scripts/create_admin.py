import getpass
import sys
from app.auth.service import hash_password
from app.core.db import get_db

if len(sys.argv) not in (2, 3):
    sys.exit("usage: python -m scripts.create_admin <username> [password]  (prompts when omitted)")
username = sys.argv[1]
# Prompting keeps the password out of shell history and the process list.
password = sys.argv[2] if len(sys.argv) == 3 else getpass.getpass(f"Password for '{username}': ")
if not password:
    sys.exit("password must not be empty")
get_db().admin_users.update_one({"username": username}, {"$set": {"password": hash_password(password)}}, upsert=True)
print(f"admin '{username}' saved")
