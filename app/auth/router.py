from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from app.auth.deps import COOKIE, require_admin
from app.auth.service import authenticate, limiter, make_session
from app.core.config import settings
from app.core.net import client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(422, "Please fill all fields!")
    key = f"{client_ip(request)}|{username.lower()}"
    if limiter.blocked(key):
        raise HTTPException(429, "Too many failed attempts — try again in 15 minutes.")
    if not authenticate(username, body.password):
        limiter.fail(key)
        raise HTTPException(401, "Wrong username or password!")
    limiter.reset(key)
    response.set_cookie(COOKIE, make_session(username), httponly=True, samesite="lax",
                        secure=settings.web_origin.startswith("https"), max_age=settings.session_ttl_hours * 3600, path="/")
    return {"username": username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(require_admin)):
    return {"username": user}
