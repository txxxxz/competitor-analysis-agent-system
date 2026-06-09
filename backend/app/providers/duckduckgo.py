from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from app.models.schemas import SearchQuery
from app.providers.errors import ProviderRequestError
from app.providers.search import SearchProvider


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        class_name = attr.get("class", "")
        if tag == "a" and "result__a" in class_name:
            self._current = {"title": "", "url": self._clean_url(attr.get("href", "")), "snippet": ""}
            self._capture_title = True
        elif self._current is not None and tag in {"a", "div"} and "result__snippet" in class_name:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
        if tag in {"a", "div"} and self._capture_snippet:
            self._capture_snippet = False
            if self._current and self._current.get("title"):
                self.results.append(self._current)
                self._current = None

    def handle_data(self, data: str) -> None:
        if not self._current:
            return
        text = " ".join(unescape(data).split())
        if not text:
            return
        if self._capture_title:
            self._current["title"] = f"{self._current.get('title', '')} {text}".strip()
        elif self._capture_snippet:
            self._current["snippet"] = f"{self._current.get('snippet', '')} {text}".strip()

    @staticmethod
    def _clean_url(value: str) -> str:
        if not value:
            return ""
        parsed = urlparse(value)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            uddg = parse_qs(parsed.query).get("uddg", [""])[0]
            return unquote(uddg)
        return value


class DuckDuckGoSearchProvider(SearchProvider):
    provider_name = "DuckDuckGoSearchProvider"

    def __init__(self, timeout_seconds: int = 5, max_results: int = 15):
        self.timeout_seconds = timeout_seconds
        self.max_results = max(1, min(max_results, 15))

    def search(self, task_id: str, query: SearchQuery, supplement: bool = False) -> list[dict]:
        html = self._fetch_html(query.query)

        parser = _DuckDuckGoHTMLParser()
        parser.feed(html)
        return [self._normalize_item(item, query) for item in parser.results[: self.max_results]]

    def _fetch_html(self, query: str) -> str:
        errors: list[str] = []
        for url in (
            f"https://duckduckgo.com/html/?q={quote_plus(query)}",
            f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}",
        ):
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
                    "Accept": "text/html,application/xhtml+xml",
                },
                method="GET",
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return response.read().decode("utf-8", errors="ignore")
            except HTTPError as exc:
                errors.append(f"{urlparse(url).netloc} HTTP {exc.code}")
            except URLError as exc:
                errors.append(f"{urlparse(url).netloc} {exc.reason}")
            except TimeoutError:
                errors.append(f"{urlparse(url).netloc} timed out")
        raise ProviderRequestError(f"DuckDuckGo request failed: {'; '.join(errors)}.")

    @staticmethod
    def _normalize_item(item: dict[str, str], query: SearchQuery) -> dict:
        content = item.get("snippet", "")
        return {
            "title": item.get("title") or query.query,
            "url": item.get("url") or "",
            "source_type": "web_search",
            "product": query.product,
            "evidence_type": query.expected_evidence,
            "summary": content[:260] or f"Search result for {query.query}",
            "locator": item.get("url") or "DuckDuckGo result",
            "content": content,
        }
