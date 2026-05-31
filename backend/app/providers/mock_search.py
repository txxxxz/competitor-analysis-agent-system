from app.fixtures.demo_data import AI_TOOLS_FIXTURES, GENERIC_FIXTURES
from app.models.schemas import SearchQuery
from app.providers.search import SearchProvider


class MockSearchProvider(SearchProvider):
    def search(self, task_id: str, query: SearchQuery, supplement: bool = False) -> list[dict]:
        if query.product == "TRAE" and supplement:
            return AI_TOOLS_FIXTURES["TRAE_SUPPLEMENT"]

        fixtures = AI_TOOLS_FIXTURES if query.product in AI_TOOLS_FIXTURES else GENERIC_FIXTURES
        candidates = fixtures.get(query.product, [])
        if query.expected_evidence == "pricing":
            return [item for item in candidates if item.get("evidence_type") == "pricing"]
        if query.expected_evidence in {"positioning", "agent_capability", "workflow"}:
            matched = [item for item in candidates if item.get("evidence_type") == query.expected_evidence]
            return matched or candidates[:1]
        return candidates[:1]
