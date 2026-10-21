"""Interface comum para fontes de vagas."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import Job, SearchFilters


class JobSource(ABC):
    name: str = "base"
    requires_auth: bool = False

    @abstractmethod
    def search(self, filters: SearchFilters) -> Iterator[Job]:
        """Busca vagas conforme os filtros, produzindo Jobs."""
