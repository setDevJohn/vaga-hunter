"""Fonte: Portal Gupy — vagas agregadas do maior ATS do Brasil (sem login).

O portal é Next.js server-rendered. A listagem JSON sai pela rota
`/_next/data/<buildId>/job-search/term=<query>&page=<n>.json`, já ordenada
por data de publicação (decrescente) e com a descrição completa — nenhum
request de detalhe é necessário. O buildId muda a cada deploy do portal,
então é extraído do HTML da página de busca e re-extraído se a rota JSON
passar a falhar.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from .base import JobSource
from .infojobs import time_range_to_timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://portal.gupy.io"

_BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')

_WORKPLACE = {"remote": True, "hybrid": False, "on-site": False, "onsite": False}


def extract_build_id(html: str) -> str:
    match = _BUILD_ID_RE.search(html)
    if not match:
        raise ValueError("Gupy: buildId não encontrado no HTML do portal")
    return match.group(1)


def search_page_url(build_id: str, keywords: str, page: int = 1) -> str:
    pid = f"term={quote(keywords.strip())}"
    if page > 1:
        pid += f"&page={page}"
    return f"{BASE_URL}/_next/data/{build_id}/job-search/{pid}.json"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_search_json(payload: str | dict) -> tuple[list[Job], int]:
    """Retorna (vagas, total anunciado pela API)."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    job_list = (data.get("pageProps") or {}).get("initialJobList") or {}
    total = int((job_list.get("pagination") or {}).get("total") or 0)
    jobs: list[Job] = []
    for item in job_list.get("data") or []:
        external_id = str(item.get("id") or "")
        if not external_id:
            continue
        description = item.get("description") or ""
        if description:
            description = BeautifulSoup(description, "lxml").get_text(" ", strip=True)
        workplace = str(item.get("workplaceType") or "").lower()
        location = ", ".join(p for p in (item.get("city"), item.get("state")) if p)
        if workplace == "remote" and not location:
            location = "Remoto"
        jobs.append(Job(
            source="gupy",
            external_id=external_id,
            title=str(item.get("name") or "").strip(),
            company=str(item.get("careerPageName") or "").strip(),
            location=location,
            url=str(item.get("jobUrl") or ""),
            description=description,
            posted_at=_parse_date(item.get("publishedDate")),
            remote=_WORKPLACE.get(workplace),
        ))
    return jobs, total


class GupySource(JobSource):
    name = "gupy"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)  # descrição já vem na listagem
        self._build_id: str | None = None
        self._yielded: set[str] = set()

    def _fetch_build_id(self) -> str:
        resp = self.session.get(f"{BASE_URL}/job-search/term=desenvolvedor")
        return extract_build_id(resp.text)

    def _fetch_page(self, keywords: str, page: int) -> tuple[list[Job], int]:
        if self._build_id is None:
            self._build_id = self._fetch_build_id()
        try:
            resp = self.session.get(search_page_url(self._build_id, keywords, page))
            return parse_search_json(resp.text)
        except (ValueError, KeyError, requests.HTTPError):
            # buildId velho (deploy novo do portal responde 404): re-extrai uma vez
            log.info("Gupy: rota _next/data falhou — renovando buildId")
            self._build_id = self._fetch_build_id()
            resp = self.session.get(search_page_url(self._build_id, keywords, page))
            return parse_search_json(resp.text)

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        for page in range(1, filters.max_pages + 1):
            jobs, _total = self._fetch_page(filters.keywords, page)
            if not jobs:
                break
            stale = unseen = 0
            for job in jobs:
                if job.uid in self._yielded:
                    continue
                unseen += 1
                if job.posted_at is not None and job.posted_at < cutoff:
                    stale += 1
                    continue  # fora da janela: não marca, outra query com
                              # janela maior ainda pode entregá-la
                self._yielded.add(job.uid)
                yield job
            # listagem vem ordenada por publishedDate desc: página com tudo
            # fora da janela encerra a query
            if unseen == 0 or stale == unseen:
                break
