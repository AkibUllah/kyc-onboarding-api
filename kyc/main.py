from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .database import Base, engine
from .errors import KycError
from .routers import applicants, health, review, stats

logger = logging.getLogger("kyc")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    Base.metadata.create_all(bind=engine)
    logger.info(
        "%s %s started storage=%s", settings.app_name, __version__, settings.storage_backend
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="KYC Onboarding API",
        version=__version__,
        description="Applicant onboarding workflow with document intake, pluggable verification "
        "checks, risk scoring, reviewer queue and SLA statistics.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s %.1fms rid=%s",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - started) * 1000,
            request_id,
        )
        return response

    @app.exception_handler(KycError)
    async def kyc_error_handler(request: Request, exc: KycError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    app.include_router(health.router)
    app.include_router(applicants.router)
    app.include_router(review.router)
    app.include_router(stats.router)
    return app


app = create_app()
