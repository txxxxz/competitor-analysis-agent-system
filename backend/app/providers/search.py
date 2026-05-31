from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.schemas import SearchQuery


class SearchProvider(ABC):
    @abstractmethod
    def search(self, task_id: str, query: SearchQuery, supplement: bool = False) -> list[dict]:
        raise NotImplementedError
