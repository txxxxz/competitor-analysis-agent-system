from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete_structured(self, purpose: str, payload: dict) -> dict:
        raise NotImplementedError
