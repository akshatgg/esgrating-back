import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.router import router as auth_router
from app.bfsi.router_admin import router as bfsi_admin_router
from app.bfsi.router_public import router as bfsi_public_router
from app.bfsi.store import ensure_indexes as ensure_bfsi_indexes
from app.contact.router import router as contact_router
from app.core.config import settings
from app.dashboard.router import router as dashboard_router
from app.core.errors import UserError
from app.core.jobs import reset_interrupted_jobs
from app.esg.router_admin import router as esg_admin_router
from app.esg.router_public import router as esg_public_router
from app.ratings.router import admin_router as ratings_admin_router
from app.ratings.router import public_router as ratings_public_router
from app.ratings.store import ensure_indexes as ensure_ratings_indexes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        reset_interrupted_jobs()
    except Exception as e:
        logging.getLogger(__name__).warning("could not reset interrupted jobs: %s", e)
    try:
        ensure_bfsi_indexes()
    except Exception as e:
        logging.getLogger(__name__).warning("could not ensure bfsi indexes: %s", e)
    try:
        ensure_ratings_indexes()
    except Exception as e:
        logging.getLogger(__name__).warning("could not ensure ratings indexes: %s", e)
    yield


app = FastAPI(title="esgratings-api", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(esg_public_router)
app.include_router(esg_admin_router)
app.include_router(bfsi_public_router)
app.include_router(bfsi_admin_router)
app.include_router(ratings_public_router)
app.include_router(ratings_admin_router)
app.include_router(contact_router)
app.include_router(dashboard_router)


@app.exception_handler(UserError)
async def _user_error(_req: Request, exc: UserError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {"status": "ok"}
