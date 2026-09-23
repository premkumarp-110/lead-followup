"""FastAPI application entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.config import settings
from app.database import close_client, ensure_indexes, ping
from app.models.schemas import UIConfig
from app.routes import alerts, callers, calls, dashboard, insights, leads
from app.scheduler import start_scheduler, stop_scheduler
from app.services.audio_service import AudioValidationError
from app.services.transcription_service import TranscriptionError
from app.services.vertex_client import VertexUnavailable

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast and loudly if MongoDB is unreachable.
    ping()
    ensure_indexes()
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    vertex_error = settings.vertex_config_error()
    crm_error = settings.lead_call_config_error()
    logger.info(
        "Connected to MongoDB database '%s'. Transcription: %s. Analysis model: %s. "
        "Audio playback: %s.",
        settings.database_name,
        settings.transcription_provider,
        settings.call_analysis_model,
        "on" if settings.audio_playback_enabled else "off",
    )
    if vertex_error:
        logger.warning(
            "Vertex AI is NOT configured -- audio transcription will fail until fixed: %s",
            vertex_error,
        )
    if crm_error:
        # Only recording playback depends on this. Everything else -- the
        # dashboard, worklist, insights, every seeded record -- works without it.
        logger.warning(
            "The Lead Call API is NOT configured -- call recordings will not play: %s", crm_error
        )

    scheduler = start_scheduler()
    email_error = settings.email_config_error()
    if settings.followup_alerts_enabled and email_error:
        logger.warning("Follow-up alerts are enabled but SMTP is not configured: %s", email_error)

    yield
    stop_scheduler(scheduler)
    close_client()


app = FastAPI(
    title="AI-Powered Lead Follow-up Management API",
    description=(
        "Upload a call recording, transcribe it, analyze the conversation with Vertex AI "
        "Gemini, and decide the next action for the lead."
    ),
    version="0.2.0",
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
app.include_router(callers.router)
app.include_router(dashboard.router)
app.include_router(alerts.router)
app.include_router(insights.router)


# --------------------------------------------------------------------------
# Error handling -- never leak a stack trace to the client
# --------------------------------------------------------------------------


@app.exception_handler(AudioValidationError)
async def audio_error_handler(request: Request, exc: AudioValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Filter/validation problems raised in our own code -> 422."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(TranscriptionError)
async def transcription_error_handler(request: Request, exc: TranscriptionError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(VertexUnavailable)
async def vertex_error_handler(request: Request, exc: VertexUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(PyMongoError)
async def mongo_error_handler(request: Request, exc: PyMongoError) -> JSONResponse:
    logger.error("MongoDB error on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable or the query failed. Check that MongoDB is running."},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Check the backend logs for details."},
    )


# --------------------------------------------------------------------------
# Meta endpoints
# --------------------------------------------------------------------------


@app.get("/api/health", tags=["health"])
def health() -> dict:
    try:
        ping()
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(exc)})
    return {
        "status": "ok",
        "database": settings.database_name,
        "vertex_configured": settings.vertex_config_error() is None,
    }


@app.get("/api/config", response_model=UIConfig, tags=["health"])
def ui_config() -> dict:
    """Non-secret settings for the frontend. No credentials, ever."""
    return {
        "audio_playback_enabled": settings.audio_playback_enabled,
        # Whether a CRM key exists at all -- never the key itself.
        "recording_source_configured": settings.lead_call_config_error() is None,
        "transcription_provider": settings.transcription_provider,
        "analysis_model": settings.call_analysis_model,
        "vertex_configured": settings.vertex_config_error() is None,
        "analysis_fallback_enabled": settings.analysis_fallback_enabled,
        "followup_alerts_enabled": settings.followup_alerts_enabled,
        "followup_alerts_email_configured": settings.email_config_error() is None,
        "followup_alert_hour": settings.followup_alert_hour,
        "followup_alert_minute": settings.followup_alert_minute,
    }


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "AI-Powered Lead Follow-up Management API", "docs": "/docs"}
