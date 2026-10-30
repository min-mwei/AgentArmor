"""Environment-backed server configuration."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARMOR_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    backend: Literal["kai", "stub"] = "kai"
    model: str = "kai"
    candidate_package: str | None = None
    package_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    served_model_name: str = "kai"
    api_key: SecretStr | None = None
    max_model_len: int = Field(default=8192, ge=16)
    scan_chunk_chars: int = Field(default=4000, ge=128, le=100_000)
    scan_profile: Literal["xpia-document-v0", "xpia-context-v1"] = "xpia-document-v0"
    scan_overlap_chars: int = Field(default=400, ge=0)
    scan_queue_size: int = Field(default=64, ge=1)
    scan_max_jobs: int = Field(default=1024, ge=1)
    scan_job_ttl_seconds: int = Field(default=3600, ge=1)
