from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "GESTION-EE API"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api"
    host: str = "0.0.0.0"
    port: int = 8000

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    # 480 min (8h) matches the NestJS contract used by the existing Angular frontend.
    jwt_access_token_ttl_minutes: int = 480
    jwt_refresh_token_ttl_days: int = 7

    cors_origins: str = "http://localhost:4200"

    # Security fix C-4 — comma-separated CIDR list of reverse-proxy IPs
    # we trust to set the `X-Forwarded-For` header. Empty in dev (we read
    # request.client.host directly). In production behind nginx/ALB, set
    # this to your proxy subnets so per-IP rate limiting actually buckets
    # by the true client IP rather than the proxy's.
    trusted_proxies: str = ""

    database_url: PostgresDsn
    database_url_sync: PostgresDsn
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    redis_url: RedisDsn
    celery_broker_url: RedisDsn
    celery_result_backend: RedisDsn

    qr_public_base_url: str = "http://localhost:4200/verify"

    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket_documents: str = "gestionee-documents"
    s3_bucket_reports: str = "gestionee-bulletins"
    s3_region: str = "us-east-1"

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    orange_api_key: str | None = None
    whatsapp_api_token: str | None = None
    whatsapp_phone_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    fcm_server_key: str | None = None

    sentry_dsn: str | None = None
    prometheus_enabled: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
