"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.config import settings
from app.database import close_client, ensure_indexes, ping
from app.routes import calls, dashboard, leads

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast and loudly if MongoDB is unreachable.
    ping()
    ensure_indexes()
    logger.info(
        "Connected to MongoDB database '%s' (analyzer backend: %s)",
        settings.database_name,
        settings.analyzer_backend,
    )
    yield
    close_client()


app = FastAPI(
    title="Lead Follow-up Management API",
    description=(
        "MVP backend for the BD follow-up dashboard. Call analysis is deterministic "
        "today and swappable for an LLM implementation later."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(leads.router)
app.include_router(calls.router)
app.include_router(dashboard.router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Filter/validation problems raised in our own code -> 422."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(PyMongoError)
async def mongo_error_handler(request: Request, exc: PyMongoError) -> JSONResponse:
    logger.error("MongoDB error on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": f"Database unavailable or query failed: {exc}"},
    )


@app.get("/api/health", tags=["health"])
def health() -> dict:
    try:
        ping()
        return {"status": "ok", "database": settings.database_name}
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "Lead Follow-up Management API", "docs": "/docs"}
