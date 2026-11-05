"""Optional OpenRouter server-side narrative & explanation layer for HydroPulse.

Architecture & Safety Rules:
1. OpenRouter is strictly an OPTIONAL server-side AI gateway (`https://openrouter.ai/api/v1/chat/completions`).
2. Hydraulic physics, leak localization, scenario simulations, pipe RUL calculations, and
   engineering recommendations in `ai_advisor.py` are 100% deterministic and NEVER depend on OpenRouter.
3. OpenRouter is used ONLY to optionally enrich human-readable `rationale` / executive summary
   text when `HYDROPULSE_OPENROUTER_ENABLED=true` and `OPENROUTER_API_KEY` is present on the server.
4. Free-tier model default: `meta-llama/llama-3.3-70b-instruct:free` (all configured models
   default to `:free` variants unless overridden).
5. Strict connect/read timeouts and bounded retries (never infinite retries).
6. Never logs API keys, authorization headers, or raw user secrets.
7. Graceful fallback: if OpenRouter is disabled, unconfigured, rate-limited (429), timed out,
   or returns invalid JSON/schema, the deterministic rationale is preserved without raising errors.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from backend.config import BackendConfig, load_config
from data_utils.schemas import AiSuggestion

log = logging.getLogger("hydropulse.openrouter")


class OpenRouterClient:
    """Bounded, fail-safe OpenRouter client for non-critical AI narrative enrichment."""

    def __init__(self, config: Optional[BackendConfig] = None) -> None:
        self._config = config or load_config()

    @property
    def config(self) -> BackendConfig:
        return self._config

    def reload_config(self, config: Optional[BackendConfig] = None) -> None:
        self._config = config or load_config()

    @property
    def is_active(self) -> bool:
        return self._config.openrouter_ready

    def get_status_metadata(self) -> Dict[str, Any]:
        """Return safe, non-secret status metadata suitable for logs or diagnostics."""
        return {
            "enabled": self._config.openrouter_enabled,
            "configured": self._config.openrouter_configured,
            "active": self._config.openrouter_ready,
            "model": self._config.openrouter_model,
            "is_free_tier_model": self._config.openrouter_model.endswith(":free"),
            "timeout_s": self._config.openrouter_timeout_s,
            "max_retries": self._config.openrouter_max_retries,
        }

    def enrich_suggestions(
        self,
        suggestions: List[AiSuggestion],
        network_revision: str,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> List[AiSuggestion]:
        """Optionally enrich suggestion rationales via OpenRouter, falling back cleanly on any error.

        Never mutates deterministic IDs, categories, target IDs, parameters, or numeric metrics.
        Only refines `rationale` if OpenRouter returns a valid concise engineering summary.
        """
        if not self.is_active or not suggestions:
            return suggestions

        # Build a compact, deterministic prompt containing only structured metrics (no secrets)
        items_payload = [
            {
                "suggestion_id": s.suggestion_id,
                "category": s.category,
                "title": s.title,
                "default_rationale": s.rationale,
            }
            for s in suggestions[:5]
        ]

        system_prompt = (
            "You are a senior water network hydraulic engineer reviewing deterministic WNTR PDD simulation "
            "recommendations. Return ONLY a JSON object mapping each `suggestion_id` to a concise 1-2 sentence "
            "technical rationale (<280 chars) preserving every exact number from `default_rationale`."
        )
        user_prompt = json.dumps(
            {"network_revision": network_revision, "suggestions": items_payload},
            separators=(",", ":"),
        )

        response_map = self._call_chat_completion_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            transport=transport,
        )
        if not response_map:
            return suggestions

        enriched: List[AiSuggestion] = []
        for s in suggestions:
            candidate_text = response_map.get(s.suggestion_id)
            if (
                isinstance(candidate_text, str)
                and 20 <= len(candidate_text.strip()) <= 500
            ):
                enriched.append(s.model_copy(update={"rationale": candidate_text.strip()}))
            else:
                enriched.append(s)
        return enriched

    def _call_chat_completion_json(
        self,
        system_prompt: str,
        user_prompt: str,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute bounded HTTP POST to OpenRouter with strict timeout and safe error handling."""
        if not self.is_active or not self._config.openrouter_api_key:
            return None

        url = f"{self._config.openrouter_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._config.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Rossonerian/hydropulse",
            "X-Title": "HydroPulse Hydraulic Digital Twin",
        }
        payload = {
            "model": self._config.openrouter_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 450,
        }

        timeout = httpx.Timeout(
            timeout=self._config.openrouter_timeout_s,
            connect=min(self._config.openrouter_connect_timeout_s, self._config.openrouter_timeout_s),
        )
        attempts = 1 + max(0, min(self._config.openrouter_max_retries, 2))

        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            try:
                with httpx.Client(timeout=timeout, transport=transport) as client:
                    resp = client.post(url, headers=headers, json=payload)

                elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)

                if resp.status_code == 429:
                    log.warning(
                        "OpenRouter rate limit (429) on model %s (attempt %d/%d, %.1f ms); using deterministic fallback.",
                        self._config.openrouter_model,
                        attempt,
                        attempts,
                        elapsed_ms,
                    )
                    return None

                if resp.status_code in (401, 403):
                    log.warning(
                        "OpenRouter authentication/permission rejected (HTTP %d); using deterministic fallback.",
                        resp.status_code,
                    )
                    return None

                if resp.status_code >= 500:
                    log.warning(
                        "OpenRouter upstream error HTTP %d on attempt %d/%d (%.1f ms).",
                        resp.status_code,
                        attempt,
                        attempts,
                        elapsed_ms,
                    )
                    if attempt < attempts:
                        continue
                    return None

                if resp.status_code != 200:
                    log.warning(
                        "OpenRouter returned unexpected status HTTP %d; using deterministic fallback.",
                        resp.status_code,
                    )
                    return None

                body = resp.json()
                choices = body.get("choices")
                if not isinstance(choices, list) or not choices:
                    log.warning("OpenRouter response missing choices array; using deterministic fallback.")
                    return None

                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if not isinstance(content, str) or not content.strip():
                    log.warning("OpenRouter response had empty message content; using deterministic fallback.")
                    return None

                parsed = _extract_json_object(content)
                if not isinstance(parsed, dict):
                    log.warning("OpenRouter content was not a valid JSON object; using deterministic fallback.")
                    return None

                return parsed

            except httpx.TimeoutException:
                log.warning(
                    "OpenRouter request timed out after %.1fs (attempt %d/%d); using deterministic fallback.",
                    self._config.openrouter_timeout_s,
                    attempt,
                    attempts,
                )
                if attempt >= attempts:
                    return None
            except Exception as exc:
                # Never log headers or keys
                log.warning(
                    "OpenRouter client error (%s) on attempt %d/%d; using deterministic fallback.",
                    type(exc).__name__,
                    attempt,
                    attempts,
                )
                return None

        return None


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from raw LLM text or fenced markdown block."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3:
            cleaned = "\n".join(lines[1:-1]).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


_default_openrouter = OpenRouterClient()


def get_openrouter_client() -> OpenRouterClient:
    return _default_openrouter
