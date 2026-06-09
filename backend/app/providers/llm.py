from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    provider_name = "LLMProvider"

    @abstractmethod
    def complete_structured(self, purpose: str, payload: dict, skill_prompt: str = "") -> dict:
        raise NotImplementedError
