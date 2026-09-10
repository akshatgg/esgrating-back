import time
from collections import defaultdict, deque
import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from app.core.config import settings
from app.core.db import get_db


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, hashed: str) -> bool:
    if hashed.startswith("$2y$"):
        hashed = "$2b$" + hashed[4:]
    try:
        return bcrypt.checkpw(p.encode(), hashed.encode())
    except ValueError:
        return False


def authenticate(username: str, password: str) -> bool:
    user = get_db().admin_users.find_one({"username": username})
    return bool(user and verify_password(password, user["password"]))


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="esg-admin")


def make_session(username: str) -> str:
    return _serializer().dumps({"u": username})


def read_session(token: str) -> str | None:
    try:
        return _serializer().loads(token, max_age=settings.session_ttl_hours * 3600)["u"]
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


class LoginLimiter:
    def __init__(self, max_failures: int = 5, window_seconds: int = 900):
        self.max, self.window = max_failures, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str) -> deque:
        q, now = self._hits[key], time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._trim(key)) >= self.max

    def fail(self, key: str) -> None:
        self._trim(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


limiter = LoginLimiter()
