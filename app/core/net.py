from fastapi import Request
from app.core.config import settings


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if not settings.trust_proxy:
        return peer
    if settings.trust_cloudflare:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf:
            return cf
    # Client-supplied entries sit on the left of X-Forwarded-For and can be forged; each
    # proxy we run appends the address it saw, so count TRUSTED_PROXY_HOPS from the right.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        n = settings.trusted_proxy_hops
        if n >= 1 and len(hops) >= n:
            return hops[-n]
    return peer
