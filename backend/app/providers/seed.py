from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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

    def complete_structured(self, purpose: str, payload: dict) -> dict:
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"Return strict JSON for {purpose}."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
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
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderRequestError(f"Seed request failed with HTTP {exc.code}.") from exc
        except URLError as exc:
            raise ProviderRequestError(f"Seed request failed: {exc.reason}.") from exc
        except json.JSONDecodeError as exc:
            raise ProviderRequestError("Seed returned invalid JSON.") from exc

        content = body.get("output") or body.get("content")
        if not content and body.get("choices"):
            content = body["choices"][0].get("message", {}).get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                raise ProviderRequestError("Seed response content was not valid JSON.") from exc
        return body
