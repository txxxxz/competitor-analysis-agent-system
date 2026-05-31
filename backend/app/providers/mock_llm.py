from app.providers.llm import LLMProvider


class MockLLMProvider(LLMProvider):
    def complete_structured(self, purpose: str, payload: dict) -> dict:
        return {"purpose": purpose, "mode": "mock", "payload_keys": sorted(payload.keys())}
