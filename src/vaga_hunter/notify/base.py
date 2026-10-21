"""Interface comum para notificadores."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Job


class Notifier(ABC):
    @abstractmethod
    def notify(self, jobs: list[Job]) -> None:
        """Envia notificação para uma lista de vagas novas (já ordenadas por score)."""
