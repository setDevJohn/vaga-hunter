"""Fonte: Remotar — vagas 100% remotas do Brasil (sem login).

A API pública (api.remotar.com.br/jobs) retorna JSON paginado (50/página)
ordenado por criação, com descrição completa em HTML e data exata
(createdAt) — nenhum request de detalhe é necessário.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from .base import JobSource
from .infojobs import time_range_to_timedelta

log = logging.getLogger(__name__)

API_URL = "https://api.remotar.com.br/jobs"
SITE_URL = "https://remotar.com.br"


def build_params(filters: SearchFilters, page: int = 1) -> dict[str, str]:
    # "search" é o parâmetro que filtra ("q" é ignorado pela API)
    params = {"search": filters.keywords.strip()}
    if page > 1:
        params["page"] = str(page)
    return params


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_search_json(payload: str | dict) -> tuple[list[Job], int]:
    """Retorna (vagas, last_page da API)."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    last_page = int((data.get("meta") or {}).get("last_page") or 1)
    jobs: list[Job] = []
    for item in data.get("data") or []:
        external_id = str(item.get("id") or "")
        if not external_id:
            continue
        description = item.get("description") or ""
        if description:
            description = BeautifulSoup(description, "lxml").get_text(" ", strip=True)
        subtitle = str(item.get("subtitle") or "").strip()
        if subtitle:
            description = f"{subtitle}\n{description}".strip()
        company = item.get("company") or {}
        company_name = str(company.get("name") or "") if isinstance(company, dict) else ""
        display_name = str(item.get("companyDisplayName") or "")
        jobs.append(Job(
            source="remotar",
            external_id=external_id,
            title=str(item.get("title") or "").strip(),
            company=(display_name or company_name).strip(),
            location="Remoto",            # site é exclusivamente de vagas remotas
            url=f"{SITE_URL}/job/{external_id}",
            description=description,
            posted_at=_parse_date(item.get("createdAt")),
            remote=True,
        ))
    return jobs, last_page


class RemotarSource(JobSource):
    name = "remotar"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)  # descrição já vem na listagem
        self._yielded: set[str] = set()

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        last_page = 1
        for page in range(1, filters.max_pages + 1):
            if page > last_page:
                break
            resp = self.session.get(API_URL, params=build_params(filters, page))
            jobs, last_page = parse_search_json(resp.text)
            if not jobs:
                break
            stale = unseen = 0
            for job in jobs:
                if job.uid in self._yielded:
                    continue
                self._yielded.add(job.uid)
                unseen += 1
                if job.posted_at is not None and job.posted_at < cutoff:
                    stale += 1
                    continue
                yield job
            # API ordena por createdAt desc: página inteira fora da janela encerra
            if unseen == 0 or stale == unseen:
                break
