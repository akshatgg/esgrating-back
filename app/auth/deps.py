from fastapi import HTTPException, Request
from app.auth.service import read_session

COOKIE = "esg_session"


def require_admin(request: Request) -> str:
    user = read_session(request.cookies.get(COOKIE, ""))
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
