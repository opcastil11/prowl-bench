"""Configuration — LLM keys, Prowl submission, execution limits."""
from __future__ import annotations

from pydantic_settings import BaseSettings


class BenchConfig(BaseSettings):
    # LLM providers (at least one required for scoring)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # Prowl submission (optional — only needed for --submit)
    prowl_agent_key: str = ""
    prowl_base_url: str = "https://prowl.world"

    # Vendor self-attestation (optional — only needed for --vendor-submit)
    # Get a JWT by logging in at https://prowl.world/app#/login and copying
    # the token from localStorage (key: prowl_jwt).
    prowl_vendor_jwt: str = ""

    # Execution limits
    max_requests_per_benchmark: int = 20
    request_timeout_seconds: int = 30
    max_payload_size_bytes: int = 10_000

    # Provider network (optional — only needed for provide command)
    prowl_wallet_address: str = ""
    prowl_wallet_type: str = "solana"

    # User agent
    user_agent: str = "prowl-bench/0.2.0"

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}


_config: BenchConfig | None = None


def get_config() -> BenchConfig:
    global _config
    if _config is None:
        _config = BenchConfig()
    return _config
