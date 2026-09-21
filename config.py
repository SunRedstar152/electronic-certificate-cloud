from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "PKRU Electronic Certificate"
    DATABASE_URL: str = Field(repr=False)
    SECRET_KEY: str = Field(min_length=32, repr=False)
    PUBLIC_BASE_URL: AnyHttpUrl = "http://localhost:8000"
    QR_UPDATE_ON_STARTUP: bool = True
    CERTIFICATE_STORAGE_FORMAT: Literal["png", "webp"] = "webp"
    WEBP_METHOD: int = Field(default=4, ge=0, le=6)
    PNG_COMPRESS_LEVEL: int = Field(default=9, ge=0, le=9)
    TEMPLATE_DIR: Path = Path("storage/uploads/templates")
    CERTIFICATE_DIR: Path = Path("storage/generated/certificates")
    FONT_DIR: Path = Path("storage/uploads/fonts")
    STORAGE_BACKEND: Literal["local", "supabase"] = "local"
    SUPABASE_URL: AnyHttpUrl | None = None
    SUPABASE_SECRET_KEY: str = Field(default="", repr=False)
    SUPABASE_STORAGE_BUCKET: str = "certificate-files"
    STORAGE_CACHE_MB: int = Field(default=256, ge=64, le=4096)
    STORAGE_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)
    REQUIRE_RAQM: bool = False
    AUDIT_RETENTION_DAYS: int = Field(default=365, ge=1, le=3650)
    ENABLE_SCHEDULER: bool = False
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    LOGIN_PAIR_MAX_FAILURES: int = Field(default=5, ge=1, le=1000)
    LOGIN_IP_MAX_FAILURES: int = Field(default=30, ge=1, le=10000)
    LOGIN_WINDOW_SECONDS: int = Field(default=15 * 60, ge=1, le=86400)
    LOGIN_CLEANUP_BATCH_SIZE: int = Field(default=100, ge=1, le=1000)

    @field_validator("DATABASE_URL")
    @classmethod
    def postgresql_only(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must use PostgreSQL.")
        return value

    @model_validator(mode="after")
    def storage_configuration(self):
        if self.STORAGE_BACKEND == "supabase":
            if not self.SUPABASE_URL or self.SUPABASE_URL.scheme != "https" or not self.SUPABASE_SECRET_KEY:
                raise ValueError("Supabase storage requires an HTTPS URL and server secret key.")
            import re
            if not re.fullmatch(r"[A-Za-z0-9_-]+", self.SUPABASE_STORAGE_BUCKET):
                raise ValueError("Invalid Supabase bucket name.")
        return self

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
