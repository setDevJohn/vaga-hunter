"""Modelos centrais: Job, SearchFilters, JobStatus."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class JobStatus(StrEnum):
    NEW = "new"
    NOTIFIED = "notified"
    INTERESTED = "interested"
    APPLIED = "applied"
    DISCARDED = "discarded"
    SEEN = "seen"


@dataclass
class Job:
    source: str                # ex.: "linkedin_jobs"
    external_id: str           # id da vaga na fonte
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    posted_at: datetime | None = None
    remote: bool | None = None

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.external_id}"


@dataclass
class SearchFilters:
    keywords: str
    location: str = "Brasil"
    time_range: str = "r24h"       # r24h | r3d | r7d | r1m
    remote: str | None = None      # remote | hybrid | onsite | None
    job_type: str | None = None
    max_pages: int = 3


@dataclass
class RunReport:
    """Resultado de uma execução do pipeline."""
    found: int = 0
    new: int = 0
    notified: int = 0
    errors: list[str] = field(default_factory=list)
