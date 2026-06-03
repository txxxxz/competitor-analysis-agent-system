from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.models.schemas import SearchQuery
from app.providers.errors import ProviderConfigurationError, ProviderRequestError
from app.providers.search import SearchProvider


class AnySearchProvider(SearchProvider):
    provider_name = "AnySearchProvider"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout_seconds: int = 20,
        max_results: int = 5,
        content_types: list[str] | None = None,
    ):
        if not api_key:
            raise ProviderConfigurationError("ANYSEARCH_API_KEY is required when mock search is disabled.")
        if not base_url:
            raise ProviderConfigurationError("ANYSEARCH_BASE_URL is required when mock search is disabled.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_results = max(1, min(max_results, 25))
        self.content_types = content_types or []

    def search(self, task_id: str, query: SearchQuery, supplement: bool = False) -> list[dict]:
        payload = {
            "query": query.query,
            "max_results": self.max_results,
        }
        if self.content_types:
            payload["content_types"] = self.content_types
        request = Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
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
            raise ProviderRequestError(f"AnySearch request failed with HTTP {exc.code}.") from exc
        except URLError as exc:
            raise ProviderRequestError(f"AnySearch request failed: {exc.reason}.") from exc
        except json.JSONDecodeError as exc:
            raise ProviderRequestError("AnySearch returned invalid JSON.") from exc

        items = body.get("results", body if isinstance(body, list) else [])
        return [self._normalize_item(item, query) for item in items if isinstance(item, dict)]

    @staticmethod
    def _normalize_item(item: dict, query: SearchQuery) -> dict:
        content = item.get("content") or item.get("description") or item.get("snippet") or item.get("summary") or ""
        return {
            "title": item.get("title") or item.get("name") or query.query,
            "url": item.get("url") or item.get("link") or "",
            "source_type": item.get("source_type") or query.source_preference or "web",
            "product": item.get("product") or query.product,
            "evidence_type": item.get("evidence_type") or query.expected_evidence,
            "summary": item.get("summary") or item.get("description") or content[:240] or query.query,
            "locator": item.get("locator") or item.get("url") or item.get("link") or "Search result",
            "content": content,
        }
