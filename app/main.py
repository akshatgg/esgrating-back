import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.router import router as auth_router
from app.core.config import settings
from app.core.errors import UserError
from app.core.jobs import reset_interrupted_jobs
from app.esg.router_admin import router as esg_admin_router
from app.esg.router_public import router as esg_public_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        reset_interrupted_jobs()
    except Exception as e:
        logging.getLogger(__name__).warning("could not reset interrupted jobs: %s", e)
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


@app.exception_handler(UserError)
async def _user_error(_req: Request, exc: UserError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {"status": "ok"}
