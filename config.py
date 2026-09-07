"""Environment-backed application settings."""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration shared by the backend and its upstream API client."""

    mfc_base_url: AnyHttpUrl
    mfc_client_id: SecretStr
    mfc_client_secret: SecretStr
    mfc_request_timeout_seconds: float = Field(default=30.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("mfc_client_id", "mfc_client_secret")
    @classmethod
    def credentials_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        invalid_fields = sorted(
            {
                str(error["loc"][0]).upper()
                for error in exc.errors()
                if error.get("loc")
            }
        )
        field_list = ", ".join(invalid_fields) or "MFC configuration"
        raise RuntimeError(
            "Backend configuration is invalid. Set valid values for "
            f"{field_list} in .env, then restart the backend."
        ) from exc


settings = _load_settings()
