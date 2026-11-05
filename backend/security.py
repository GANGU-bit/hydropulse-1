"""Security boundary, request tracing, payload size guards, and error sanitization.

Endpoint Classification Matrix:
1. Public Read-Only (`GET`):
   - `/api/health`
   - `/api/ready`
   - `/api/network`
   - `/api/presets`
   - `/api/network/presets`
   - `/api/ai/suggestions`
   - `/api/iot/state`
   - `/api/pipes/condition`
2. Public Simulation / Preview (Stateless or Bounded Preview Cache, Non-Mutating to Active Twin):
   - `/api/simulate-leak`
   - `/api/isolate`
   - `/api/what-if`
   - `/api/network/import/preview`
   - `/api/network/build/preview`
   - `/api/network/intellisense`
3. Administrative / Mutating Operations (Protected by `require_admin_access` when enabled):
   - `/api/ai/apply-suggestion`
   - `/api/network/import/commit`
   - `/api/network/import`
   - `/api/network/build/commit`
   - `/api/network/reset`
   - `/api/iot/ingest`
   - `/api/iot/auto-valve`

Authentication Boundary:
- CORS is browser cross-origin access control, NEVER authentication.
- When `HYDROPULSE_REQUIRE_ADMIN_AUTH=true` (or when `HYDROPULSE_ADMIN_TOKEN` is set
  and `HYDROPULSE_REQUIRE_ADMIN_AUTH` is not explicitly disabled in `prod`), mutating
  administrative routes require either:
  * `Authorization: Bearer <HYDROPULSE_ADMIN_TOKEN>`
  * `X-Admin-Token: <HYDROPULSE_ADMIN_TOKEN>`
- Comparison uses `hmac.compare_digest` to prevent timing side-channels.
- Secrets are never placed in frontend `VITE_*` variables; in production deployments,
  an upstream reverse proxy / API gateway or operator CLI supplies the header.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import re
import time
import uuid
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import BackendConfig

log = logging.getLogger("hydropulse.security")

_PATH_LEAK_RE = re.compile(r"(/[a-zA-Z0-9._-]+){3,}")


def sanitize_error_detail(detail: Any, is_prod: bool = False) -> Any:
    """Redact local filesystem paths or sensitive tokens from error details in production."""
    if not is_prod:
        return detail
    if isinstance(detail, str):
        return _PATH_LEAK_RE.sub("[redacted-path]", detail)
    return detail


def _contains_non_finite_floats(obj: Any) -> bool:
    """Recursively check decoded JSON object for NaN or +/-Infinity."""
    if isinstance(obj, float):
        return math.isnan(obj) or math.isinf(obj)
    if isinstance(obj, dict):
        return any(_contains_non_finite_floats(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_non_finite_floats(v) for v in obj)
    return False


def make_admin_dependency(get_config: Callable[[], BackendConfig]) -> Callable[[Request], None]:
    """Create a FastAPI dependency that enforces the administrative token boundary when enabled."""

    def verify_admin(request: Request) -> None:
        cfg = get_config()
        if not cfg.require_admin_auth:
            return

        expected = cfg.admin_token or ""
        if not expected:
            log.error("Admin auth is enabled but HYDROPULSE_ADMIN_TOKEN is missing")
            raise HTTPException(
                status_code=503,
                detail="Administrative actions are disabled until server authentication is configured.",
            )

        provided = ""
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            provided = auth_header[7:].strip()
        if not provided:
            provided = request.headers.get("X-Admin-Token", "").strip()

        if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(
                status_code=401,
                detail="Administrative authentication required (supply Authorization: Bearer <token> or X-Admin-Token).",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify_admin


class SecurityAndTracingMiddleware(BaseHTTPMiddleware):
    """Enforces request size limits, strict JSON NaN/Infinity rejection, Request-ID tracing, and security headers."""

    def __init__(self, app: Any, get_config: Callable[[], BackendConfig]) -> None:
        super().__init__(app)
        self._get_config = get_config

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        cfg = self._get_config()
        req_id = request.headers.get("X-Request-ID", "").strip() or uuid.uuid4().hex[:16]
        # Sanitise custom request IDs to safe printable characters
        req_id = re.sub(r"[^a-zA-Z0-9._-]", "", req_id)[:64] or uuid.uuid4().hex[:16]
        request.state.request_id = req_id
        t0 = time.perf_counter()

        # 1. Check Content-Length header fast-path
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > cfg.max_request_body_bytes:
                    return self._json_error(
                        status_code=413,
                        detail=f"Request body exceeds maximum allowed size of {cfg.max_request_body_bytes} bytes.",
                        req_id=req_id,
                    )
            except ValueError:
                return self._json_error(
                    status_code=400,
                    detail="Invalid Content-Length header.",
                    req_id=req_id,
                )

        # 2. For mutating/POST/PUT/PATCH requests, inspect actual body length and reject NaN/Infinity JSON
        if request.method in ("POST", "PUT", "PATCH"):
            chunks: list[bytes] = []
            total_bytes = 0
            async for chunk in request.stream():
                total_bytes += len(chunk)
                if total_bytes > cfg.max_request_body_bytes:
                    return self._json_error(
                        status_code=413,
                        detail=f"Request body ({total_bytes} bytes) exceeds maximum allowed size ({cfg.max_request_body_bytes} bytes).",
                        req_id=req_id,
                    )
                chunks.append(chunk)
            body = b"".join(chunks)
            request._body = body
            content_type = request.headers.get("content-type", "").lower()
            if body and "application/json" in content_type:
                try:
                    parsed = json.loads(
                        body.decode("utf-8"),
                        parse_constant=lambda c: (_ for _ in ()).throw(
                            ValueError(f"Non-finite JSON constant '{c}' is not allowed.")
                        ),
                    )
                    if _contains_non_finite_floats(parsed):
                        return self._json_error(
                            status_code=422,
                            detail="Non-finite float values (NaN/Infinity) are not allowed.",
                            req_id=req_id,
                        )
                except UnicodeDecodeError:
                    return self._json_error(
                        status_code=422,
                        detail="Request body is not valid UTF-8 encoded JSON.",
                        req_id=req_id,
                    )
                except ValueError as exc:
                    if "Non-finite JSON constant" in str(exc):
                        return self._json_error(
                            status_code=422,
                            detail=str(exc),
                            req_id=req_id,
                        )
                    # Let FastAPI handle standard JSON syntax errors so 422 shape matches contract

        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"

        log.debug(
            "req_id=%s method=%s path=%s status=%d duration_ms=%.1f",
            req_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    @staticmethod
    def _json_error(status_code: int, detail: str, req_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers={
                "X-Request-ID": req_id,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            },
        )


def register_exception_handlers(app: FastAPI, get_config: Callable[[], BackendConfig]) -> None:
    """Install predictable error handlers that never leak stack traces or secrets."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        cfg = get_config()
        req_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]
        headers = dict(exc.headers or {})
        headers["X-Request-ID"] = req_id
        headers["X-Content-Type-Options"] = "nosniff"
        detail = sanitize_error_detail(exc.detail, is_prod=cfg.is_prod)
        return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]
        # Clean non-serializable objects in errors['ctx']
        cleaned_errors = []
        for err in exc.errors():
            err_copy = dict(err)
            if "ctx" in err_copy and isinstance(err_copy["ctx"], dict):
                err_copy["ctx"] = {k: str(v) for k, v in err_copy["ctx"].items()}
            cleaned_errors.append(err_copy)
        return JSONResponse(
            status_code=422,
            content={"detail": cleaned_errors},
            headers={"X-Request-ID": req_id, "X-Content-Type-Options": "nosniff"},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        cfg = get_config()
        req_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex[:16]
        log.exception("Unhandled exception on %s %s (req_id=%s)", request.method, request.url.path, req_id)
        if cfg.is_prod:
            detail = f"Internal server error (request_id={req_id})."
        else:
            detail = f"Simulation failed: {exc}"
        return JSONResponse(
            status_code=500,
            content={"detail": detail},
            headers={"X-Request-ID": req_id, "X-Content-Type-Options": "nosniff"},
        )
