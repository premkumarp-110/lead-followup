"""Application configuration, loaded from the environment / .env file.

Nothing here has a hardcoded MongoDB URL or a hardcoded credential -- if a
required value is missing the app refuses to start with a clear message rather
than silently using a default.

Vertex AI settings are validated lazily (see `vertex_config_error`) because the
app must still boot, serve the dashboard and seed data when Google Cloud has
not been configured yet. Only the AI-dependent routes fail in that case.
"""

import json
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- MongoDB (required) ------------------------------------------------
    mongodb_url: str = Field(..., alias="MONGODB_URL")
    database_name: str = Field(..., alias="DATABASE_NAME")

    # ---- Vertex AI / Gemini ------------------------------------------------
    # Blank until the operator configures Google Cloud. `vertex_config_error`
    # turns a blank value into a readable message at call time.
    google_cloud_project: str = Field("", alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field("us-central1", alias="GOOGLE_CLOUD_LOCATION")
    vertex_ai_model: str = Field("gemini-2.5-flash", alias="VERTEX_AI_MODEL")
    google_application_credentials: str = Field("", alias="GOOGLE_APPLICATION_CREDENTIALS")
    # On platforms with no writable/persistent path to hand GOOGLE_APPLICATION_CREDENTIALS
    # (e.g. Vercel), paste the service-account key's raw JSON here instead. It's written to a
    # temp file once per cold start and google_application_credentials is pointed at it below.
    google_credentials_json: str = Field("", alias="GOOGLE_CREDENTIALS_JSON")
    google_genai_use_vertexai: bool = Field(True, alias="GOOGLE_GENAI_USE_VERTEXAI")

    # ---- Call-outcome analysis (OpenAI-compatible chat-completions) --------
    # Separate from Vertex AI above -- this is only for turning a transcript
    # into outcome/follow-up JSON. `call_analysis_config_error` turns a blank
    # value into a readable message at call time, same pattern as Vertex AI.
    call_analysis_api_url: str = Field(
        "https://ai.hyrenet-staging.in/v1/chat/completions", alias="CALL_ANALYSIS_API_URL"
    )
    call_analysis_api_key: str = Field("", alias="CALL_ANALYSIS_API_KEY")
    call_analysis_model: str = Field("global.anthropic.claude-sonnet-5", alias="CALL_ANALYSIS_MODEL")

    # ---- Feature gate --------------------------------------------------------
    # The entire "Analyze New Call" section (UI) and its ingestion/processing
    # endpoints are hidden/disabled unless this is explicitly set to true.
    # Default false: "only if the variable in the env is specified".
    call_analyzer_enabled: bool = Field(False, alias="CALL_ANALYZER_ENABLED")

    # ---- Pipeline ----------------------------------------------------------
    transcription_provider: str = Field("vertex", alias="TRANSCRIPTION_PROVIDER")
    # When Gemini fails, fall back to the deterministic keyword analyzer so the
    # pipeline still completes. Set false for strict "AI or nothing" behaviour.
    analysis_fallback_enabled: bool = Field(True, alias="ANALYSIS_FALLBACK_ENABLED")

    # ---- Audio -------------------------------------------------------------
    audio_storage_mode: str = Field("local", alias="AUDIO_STORAGE_MODE")
    audio_playback_enabled: bool = Field(True, alias="AUDIO_PLAYBACK_ENABLED")
    audio_upload_dir: str = Field("uploads", alias="AUDIO_UPLOAD_DIR")
    max_audio_mb: int = Field(20, alias="MAX_AUDIO_MB", ge=1, le=100)
    allowed_audio_types: str = Field("mp3,wav,m4a,ogg,webm", alias="ALLOWED_AUDIO_TYPES")

    cors_origins: str = Field("http://localhost:5173", alias="CORS_ORIGINS")

    @field_validator("mongodb_url", "database_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @field_validator("audio_storage_mode")
    @classmethod
    def _valid_storage_mode(cls, value: str) -> str:
        mode = (value or "local").strip().lower()
        if mode not in {"local", "url"}:
            raise ValueError("AUDIO_STORAGE_MODE must be 'local' or 'url'")
        return mode

    @model_validator(mode="after")
    def _materialize_credentials_json(self) -> "Settings":
        if self.google_credentials_json.strip() and not self.google_application_credentials.strip():
            fd, path = tempfile.mkstemp(prefix="gcp-credentials-", suffix=".json")
            with open(fd, "w") as handle:
                json.dump(json.loads(self.google_credentials_json), handle)
            self.google_application_credentials = path
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_audio_extensions(self) -> list[str]:
        return [
            ext.strip().lower().lstrip(".")
            for ext in self.allowed_audio_types.split(",")
            if ext.strip()
        ]

    @property
    def max_audio_bytes(self) -> int:
        return self.max_audio_mb * 1024 * 1024

    @property
    def upload_path(self) -> Path:
        """Absolute upload directory, resolved relative to backend/ when needed."""
        path = Path(self.audio_upload_dir)
        return path if path.is_absolute() else BACKEND_DIR / path

    def vertex_config_error(self) -> str | None:
        """Return a readable reason Vertex AI cannot be used, or None if it can.

        Checked at call time rather than at startup so the dashboard, seeding
        and every non-AI endpoint keep working on an unconfigured machine.
        """
        if not self.google_cloud_project.strip():
            return (
                "GOOGLE_CLOUD_PROJECT is not set in backend/.env. Set it to a Google Cloud "
                "project that has the Vertex AI API (aiplatform.googleapis.com) enabled."
            )
        if not self.vertex_ai_model.strip():
            return "VERTEX_AI_MODEL is not set in backend/.env (e.g. gemini-2.5-flash)."
        if not self.google_cloud_location.strip():
            return "GOOGLE_CLOUD_LOCATION is not set in backend/.env (e.g. us-central1)."
        creds = self.google_application_credentials.strip()
        if creds and not Path(creds).expanduser().exists():
            return (
                f"GOOGLE_APPLICATION_CREDENTIALS points to '{creds}', which does not exist. "
                "Fix the path, or leave it blank to use Application Default Credentials."
            )
        return None

    def call_analysis_config_error(self) -> str | None:
        """Return a readable reason the call-outcome analysis endpoint cannot be
        used, or None if it can.

        Checked at call time, not at startup, so the app boots and
        transcription keeps working even when this isn't configured yet.
        """
        if not self.call_analysis_api_url.strip():
            return "CALL_ANALYSIS_API_URL is not set in backend/.env."
        if not self.call_analysis_api_key.strip():
            return (
                "CALL_ANALYSIS_API_KEY is not set in backend/.env. Set it to a valid API key "
                "for the call-outcome analysis endpoint."
            )
        if not self.call_analysis_model.strip():
            return (
                "CALL_ANALYSIS_MODEL is not set in backend/.env "
                "(e.g. global.anthropic.claude-sonnet-5)."
            )
        return None


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except Exception as exc:  # pragma: no cover - startup guard
        raise RuntimeError(
            "Configuration error: could not load MONGODB_URL / DATABASE_NAME. "
            f"Copy backend/.env.example to backend/.env and fill it in. ({exc})"
        ) from exc


settings = get_settings()
