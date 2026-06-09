from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.providers.deepseek import PURPOSE_SCHEMAS
from app.providers.errors import ProviderConfigurationError, ProviderRequestError
from app.providers.llm import LLMProvider


class SeedLLMProvider(LLMProvider):
    provider_name = "SeedLLMProvider"

    def __init__(self, api_key: str, base_url: str, model: str, timeout_seconds: int = 30):
        if not api_key:
            raise ProviderConfigurationError("SEED_API_KEY is required when mock LLM is disabled.")
        if not base_url:
            raise ProviderConfigurationError("SEED_BASE_URL is required when mock LLM is disabled.")
        if not model:
            raise ProviderConfigurationError("SEED_MODEL is required when mock LLM is disabled.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def complete_structured(self, purpose: str, payload: dict, skill_prompt: str = "") -> dict:
        schema_instruction = PURPOSE_SCHEMAS.get(purpose, "Return strict JSON only.")
        hard_constraints = (
            "Hard constraints: return only valid JSON; bind factual claims to Evidence IDs when evidence is present; "
            "do not invent facts, metrics, users, pricing, or sources; do not bypass Review Ticket gaps; "
            "the JSON schema and evidence rules override any PM skill guidance."
        )
        skill_section = f"\n\nPM skill markdown framework:\n{skill_prompt}" if skill_prompt else ""
        request_body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You are a structured-output assistant for {purpose}. "
                        f"{schema_instruction} {hard_constraints}{skill_section}"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.2,
        }
        request = Request(
            self.base_url,
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                request_id = response.headers.get("x-request-id") or ""
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:500]
            raise ProviderRequestError(f"Seed request failed with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderRequestError(f"Seed request failed: {exc.reason}.") from exc
        except json.JSONDecodeError as exc:
            raise ProviderRequestError("Seed returned invalid JSON.") from exc

        content = body.get("output") or body.get("content")
        if not content and body.get("choices"):
            content = body["choices"][0].get("message", {}).get("content")
        if isinstance(content, dict):
            parsed = content
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ProviderRequestError("Seed response content was not valid JSON.") from exc
        elif isinstance(body, dict):
            parsed = body
        else:
            raise ProviderRequestError("Seed returned an unsupported response shape.")

        if isinstance(parsed, dict):
            parsed.setdefault(
                "__provider_meta",
                {
                    "request_id": request_id or body.get("id", ""),
                    "usage": body.get("usage", {}),
                    "model": body.get("model", self.model),
                },
            )
            return parsed
        raise ProviderRequestError("Seed parsed content was not a JSON object.")
