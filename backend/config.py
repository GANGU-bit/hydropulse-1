"""Centralized configuration and environment validation for HydroPulse backend.

Provides a clean separation between development (`dev`) and production (`prod`)
environments while resolving relative paths deterministically from `REPO_ROOT`
regardless of the current working directory.

Secrets (such as `OPENROUTER_API_KEY` and `HYDROPULSE_ADMIN_TOKEN`) are never
exposed in `safe_summary()` or exception messages.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("hydropulse.config")

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def _parse_bool(val: str | None, default: bool = False) -> bool:
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(name: str, val: str | None, default: int, min_val: int = 1, max_val: int = 100_000_000) -> int:
    if val is None or val.strip() == "":
        return default
    try:
        parsed = int(val.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {name}: {val!r}") from exc
    if not (min_val <= parsed <= max_val):
        raise ValueError(f"{name}={parsed} out of valid range [{min_val}, {max_val}]")
    return parsed


def _parse_float(
    name: str, val: str | None, default: float, min_val: float = 0.0, max_val: float = 1_000_000.0
) -> float:
    if val is None or val.strip() == "":
        return default
    try:
        parsed = float(val.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid float for {name}: {val!r}") from exc
    if not (min_val <= parsed <= max_val):
        raise ValueError(f"{name}={parsed} out of valid range [{min_val}, {max_val}]")
    return parsed


def resolve_repo_path(path_str: str | Path) -> Path:
    """Resolve a path relative to REPO_ROOT if it is not already absolute."""
    p = Path(path_str)
    if p.is_absolute():
        return p
    return (REPO_ROOT / p).resolve()


@dataclass(frozen=True)
class BackendConfig:
    """Immutable runtime configuration for the HydroPulse backend."""

    env: str = "dev"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    reload: bool = False

    # CORS configuration
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    )

    # File paths (always resolved against REPO_ROOT when relative)
    inp_path: Path = field(default_factory=lambda: resolve_repo_path("backend/bangalore_kengeri.inp"))
    meta_path: Path = field(default_factory=lambda: resolve_repo_path("backend/network_meta.json"))
    presets_path: Path = field(default_factory=lambda: resolve_repo_path("data_utils/presets.json"))
    cache_dir: Path = field(default_factory=lambda: resolve_repo_path("backend"))
    state_dir: Path = field(default_factory=lambda: resolve_repo_path("backend/.state"))
    persist_active_state: bool = False

    # Security and administrative boundary
    require_admin_auth: bool = False
    admin_token: str | None = None
    max_request_body_bytes: int = 2_621_440  # 2.5 MB ceiling (supports 2 MB INP/GeoJSON JSON payload)
    docs_enabled: bool = True

    # OpenRouter exclusive AI gateway configuration
    openrouter_enabled: bool = True
    openrouter_api_key: str | None = None
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    openrouter_fallback_models: tuple[str, ...] = (
        "qwen/qwen-2.5-72b-instruct:free",
        "mistralai/mistral-small-3.1-24b-instruct:free",
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_s: float = 4.0
    openrouter_connect_timeout_s: float = 2.0
    openrouter_max_retries: int = 1

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key and self.openrouter_api_key.strip())

    @property
    def openrouter_ready(self) -> bool:
        return bool(self.openrouter_enabled and self.openrouter_configured)

    def validate(self) -> None:
        """Validate configuration invariants; raise ValueError with actionable message on failure."""
        if self.env not in ("dev", "prod", "test"):
            raise ValueError(
                f"Invalid HYDROPULSE_ENV={self.env!r}. Must be one of: 'dev', 'prod', 'test'."
            )

        if self.is_prod and self.reload:
            raise ValueError(
                "Development reload mode (HYDROPULSE_RELOAD=true) is forbidden when HYDROPULSE_ENV=prod."
            )

        if not self.cors_origins:
            raise ValueError("HYDROPULSE_CORS_ORIGINS must specify at least one origin.")

        if self.is_prod and "*" in self.cors_origins:
            raise ValueError(
                "Wildcard CORS origin ('*') is forbidden when HYDROPULSE_ENV=prod. "
                "Specify explicit origins in HYDROPULSE_CORS_ORIGINS."
            )

        if self.require_admin_auth and (not self.admin_token or len(self.admin_token.strip()) < 8):
            raise ValueError(
                "HYDROPULSE_REQUIRE_ADMIN_AUTH=true requires a non-empty HYDROPULSE_ADMIN_TOKEN "
                "(at least 8 characters)."
            )

        # Reject any accidental non-OpenRouter provider URL
        lower_url = self.openrouter_base_url.lower()
        for forbidden in ("generativelanguage.googleapis.com", "api.groq.com", "api.openai.com", "api.anthropic.com"):
            if forbidden in lower_url:
                raise ValueError(
                    f"Direct provider URL {self.openrouter_base_url!r} is forbidden. "
                    "OpenRouter is the exclusive AI gateway for HydroPulse."
                )

    def safe_summary(self) -> dict[str, Any]:
        """Return a redacted configuration dictionary safe for logging and diagnostics."""
        return {
            "env": self.env,
            "host": self.host,
            "port": self.port,
            "log_level": self.log_level,
            "reload": self.reload,
            "cors_origins": list(self.cors_origins),
            "inp_path": str(self.inp_path),
            "meta_path": str(self.meta_path),
            "presets_path": str(self.presets_path),
            "cache_dir": str(self.cache_dir),
            "state_dir": str(self.state_dir),
            "persist_active_state": self.persist_active_state,
            "require_admin_auth": self.require_admin_auth,
            "admin_token_configured": bool(self.admin_token),
            "max_request_body_bytes": self.max_request_body_bytes,
            "docs_enabled": self.docs_enabled,
            "openrouter_enabled": self.openrouter_enabled,
            "openrouter_api_key_configured": bool(self.openrouter_api_key),
            "openrouter_model": self.openrouter_model,
            "openrouter_timeout_s": self.openrouter_timeout_s,
        }


def load_config(environ: dict[str, str] | None = None) -> BackendConfig:
    """Load and validate BackendConfig from environment variables (or provided dict)."""
    env_map = environ if environ is not None else os.environ

    env_raw = env_map.get("HYDROPULSE_ENV", "dev").strip().lower() or "dev"
    if env_raw == "production":
        env_raw = "prod"
    elif env_raw == "development":
        env_raw = "dev"

    is_prod = env_raw == "prod"

    host = env_map.get("HYDROPULSE_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = _parse_int("HYDROPULSE_PORT", env_map.get("HYDROPULSE_PORT"), default=8000, min_val=1, max_val=65535)
    log_level = (env_map.get("HYDROPULSE_LOG_LEVEL", "INFO").strip() or "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"Invalid HYDROPULSE_LOG_LEVEL={log_level!r}.")

    reload_flag = _parse_bool(env_map.get("HYDROPULSE_RELOAD"), default=False)

    default_origins = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    )
    raw_origins = env_map.get("HYDROPULSE_CORS_ORIGINS", "")
    if is_prod and not raw_origins.strip():
        raise ValueError(
            "HYDROPULSE_CORS_ORIGINS must specify the exact production frontend origin(s)."
        )
    if raw_origins.strip():
        cors_origins = tuple(
            o.strip().rstrip("/") if o.strip() != "*" else "*"
            for o in raw_origins.split(",")
            if o.strip()
        )
    else:
        cors_origins = default_origins

    inp_raw = env_map.get("HYDROPULSE_INP", "backend/bangalore_kengeri.inp").strip()
    meta_raw = env_map.get("HYDROPULSE_META", "backend/network_meta.json").strip()
    presets_raw = env_map.get("HYDROPULSE_PRESETS", "data_utils/presets.json").strip()
    cache_dir_raw = env_map.get("HYDROPULSE_CACHE_DIR", "backend").strip()
    state_dir_raw = env_map.get("HYDROPULSE_STATE_DIR", "backend/.state").strip()

    persist_state = _parse_bool(env_map.get("HYDROPULSE_PERSIST_ACTIVE_STATE"), default=is_prod)

    admin_token_raw = env_map.get("HYDROPULSE_ADMIN_TOKEN", "").strip()
    admin_token = admin_token_raw if admin_token_raw else None
    require_admin_auth = _parse_bool(
        env_map.get("HYDROPULSE_REQUIRE_ADMIN_AUTH"),
        default=is_prod,
    )

    max_body = _parse_int(
        "HYDROPULSE_MAX_REQUEST_BYTES",
        env_map.get("HYDROPULSE_MAX_REQUEST_BYTES"),
        default=2_621_440,
        min_val=65_536,
        max_val=20_971_520,
    )

    docs_enabled = _parse_bool(env_map.get("HYDROPULSE_DOCS_ENABLED"), default=not is_prod)

    or_enabled_raw = env_map.get("HYDROPULSE_OPENROUTER_ENABLED", env_map.get("OPENROUTER_ENABLED"))
    openrouter_enabled = _parse_bool(or_enabled_raw, default=True)
    openrouter_key_raw = (
        env_map.get("OPENROUTER_API_KEY") or env_map.get("HYDROPULSE_OPENROUTER_API_KEY") or ""
    ).strip()
    openrouter_api_key = openrouter_key_raw if openrouter_key_raw else None
    openrouter_model = (
        (env_map.get("HYDROPULSE_OPENROUTER_MODEL") or env_map.get("OPENROUTER_MODEL") or "meta-llama/llama-3.3-70b-instruct:free").strip()
        or "meta-llama/llama-3.3-70b-instruct:free"
    )
    raw_fallbacks = env_map.get("HYDROPULSE_OPENROUTER_FALLBACK_MODELS") or env_map.get("OPENROUTER_FALLBACK_MODELS") or ""
    if raw_fallbacks.strip():
        fallback_models = tuple(m.strip() for o in raw_fallbacks.split(",") if (m := o.strip()))
    else:
        fallback_models = (
            "qwen/qwen-2.5-72b-instruct:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",
        )
    openrouter_base_url = (
        (env_map.get("HYDROPULSE_OPENROUTER_BASE_URL") or env_map.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").strip().rstrip("/")
        or "https://openrouter.ai/api/v1"
    )
    openrouter_timeout_s = _parse_float(
        "HYDROPULSE_OPENROUTER_TIMEOUT_S",
        env_map.get("HYDROPULSE_OPENROUTER_TIMEOUT_S", env_map.get("OPENROUTER_TIMEOUT_S")),
        default=4.0,
        min_val=0.2,
        max_val=30.0,
    )
    openrouter_connect_timeout_s = _parse_float(
        "HYDROPULSE_OPENROUTER_CONNECT_TIMEOUT_S",
        env_map.get("HYDROPULSE_OPENROUTER_CONNECT_TIMEOUT_S", env_map.get("OPENROUTER_CONNECT_TIMEOUT_S")),
        default=2.0,
        min_val=0.1,
        max_val=15.0,
    )
    openrouter_max_retries = _parse_int(
        "HYDROPULSE_OPENROUTER_MAX_RETRIES",
        env_map.get("HYDROPULSE_OPENROUTER_MAX_RETRIES", env_map.get("OPENROUTER_MAX_RETRIES")),
        default=1,
        min_val=0,
        max_val=3,
    )

    cfg = BackendConfig(
        env=env_raw,
        host=host,
        port=port,
        log_level=log_level,
        reload=reload_flag,
        cors_origins=cors_origins,
        inp_path=resolve_repo_path(inp_raw),
        meta_path=resolve_repo_path(meta_raw),
        presets_path=resolve_repo_path(presets_raw),
        cache_dir=resolve_repo_path(cache_dir_raw),
        state_dir=resolve_repo_path(state_dir_raw),
        persist_active_state=persist_state,
        require_admin_auth=require_admin_auth,
        admin_token=admin_token,
        max_request_body_bytes=max_body,
        docs_enabled=docs_enabled,
        openrouter_enabled=openrouter_enabled,
        openrouter_api_key=openrouter_api_key,
        openrouter_model=openrouter_model,
        openrouter_fallback_models=fallback_models,
        openrouter_base_url=openrouter_base_url,
        openrouter_timeout_s=openrouter_timeout_s,
        openrouter_connect_timeout_s=openrouter_connect_timeout_s,
        openrouter_max_retries=openrouter_max_retries,
    )
    cfg.validate()
    return cfg
