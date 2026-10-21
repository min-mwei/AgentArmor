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
    backend: Literal["vllm", "reference", "stub"] = "vllm"
    model: str = "min-h-wei/Kai"
    revision: str | None = "d0d6a5694d0001e06b383f936c9bd8ff77271bf7"
    reference_url: str = "http://127.0.0.1:8009"
    served_model_name: str = "kai"
    api_key: SecretStr | None = None
    dtype: Literal["auto", "bfloat16", "float16", "float32"] = "bfloat16"
    tensor_parallel_size: int = Field(default=1, ge=1)
    gpu_memory_utilization: float = Field(default=0.85, gt=0, lt=1)
    kv_cache_memory_mb: int | None = Field(default=None, ge=16)
    max_model_len: int = Field(default=8192, ge=16)
    max_num_seqs: int = Field(default=8, ge=1)
    scan_chunk_chars: int = Field(default=4000, ge=128, le=100_000)
    scan_overlap_chars: int = Field(default=400, ge=0)
    scan_queue_size: int = Field(default=64, ge=1)
    scan_max_jobs: int = Field(default=1024, ge=1)
    scan_job_ttl_seconds: int = Field(default=3600, ge=1)
