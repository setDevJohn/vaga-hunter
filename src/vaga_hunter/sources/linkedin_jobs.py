"""Fonte: busca pública de vagas do LinkedIn (endpoints guest, sem login).

Baseado na lógica comprovada do bot antigo (Linkedin-vagas/scraper.py),
reescrito com parsers puros testáveis e sessão educada.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from datetime import datetime

from bs4 import BeautifulSoup

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from .base import JobSource

log = logging.getLogger(__name__)

PAGE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 25

TIME_RANGES = {  # f_TPR em segundos
    "r24h": "r86400",
    "r3d": "r259200",
    "r7d": "r604800",
    "r1m": "r2592000",
}
WORKPLACE = {"onsite": "1", "remote": "2", "hybrid": "3"}  # f_WT


def build_params(filters: SearchFilters, start: int = 0) -> dict[str, str]:
    """Monta a query string da busca guest (mesma semântica do bot antigo)."""
    keywords = filters.keywords.strip().strip('"')
    keywords = re.sub(r"\bjr\b\.?", "junior", keywords, flags=re.IGNORECASE)
    if " " in keywords:
        keywords = f'"{keywords}"'

    location = filters.location.strip()
    if location and not re.search(r"brasil|brazil", location, re.IGNORECASE):
        location = f"{location}, Brasil"

    params = {
        "keywords": keywords,
        "location": location or "Brasil",
        "f_TPR": TIME_RANGES.get(filters.time_range, TIME_RANGES["r7d"]),
        "sortBy": "DD",
        "start": str(start),
    }
    if filters.remote and filters.remote in WORKPLACE:
        params["f_WT"] = WORKPLACE[filters.remote]
    if filters.job_type:
        params["f_JT"] = filters.job_type
    return params


def parse_search_page(html: str) -> list[Job]:
    """Extrai cards de vaga de uma página de resultados guest.

    O card já traz título, empresa, local, data e URL — o detalhe só é
    necessário para a descrição.
    """
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    for card in soup.select("[data-entity-urn]"):
        urn = card.get("data-entity-urn", "")
        match = re.search(r"jobPosting:(\d+)", urn)
        if not match:
            continue
        job_id = match.group(1)

        title_el = card.select_one(".base-search-card__title")
        company_el = card.select_one(".base-search-card__subtitle")
        location_el = card.select_one(".job-search-card__location")
        link_el = card.select_one("a.base-card__full-link")
        time_el = card.select_one("time[datetime]")

        posted_at = None
        if time_el:
            try:
                posted_at = datetime.fromisoformat(time_el["datetime"])
            except (ValueError, KeyError):
                pass

        url = ""
        if link_el and link_el.get("href"):
            url = link_el["href"].split("?")[0]

        jobs.append(Job(
            source="linkedin_jobs",
            external_id=job_id,
            title=title_el.get_text(strip=True) if title_el else "",
            company=company_el.get_text(strip=True) if company_el else "",
            location=location_el.get_text(strip=True) if location_el else "",
            url=url or f"https://www.linkedin.com/jobs/view/{job_id}",
            posted_at=posted_at,
        ))
    return jobs


def parse_job_detail(html: str) -> str:
    """Extrai a descrição da página de detalhe guest."""
    soup = BeautifulSoup(html, "lxml")
    desc = soup.select_one(".show-more-less-html__markup") \
        or soup.select_one(".description__text")
    return desc.get_text(" ", strip=True) if desc else ""


class LinkedInJobsSource(JobSource):
    name = "linkedin_jobs"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        """skip_detail(uid) -> True pula o fetch da descrição (vaga já conhecida)."""
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        for page in range(filters.max_pages):
            start = page * PAGE_SIZE
            resp = self.session.get(PAGE_URL, params=build_params(filters, start))
            jobs = parse_search_page(resp.text)
            if not jobs:
                if page == 0 and resp.text.strip():
                    log.warning("página não-vazia com 0 vagas parseadas — "
                                "markup do LinkedIn pode ter mudado")
                break
            for job in jobs:
                if not self.skip_detail(job.uid):
                    job.description = self._fetch_description(job.external_id)
                yield job
            if len(jobs) < PAGE_SIZE:
                break

    def _fetch_description(self, job_id: str) -> str:
        try:
            resp = self.session.get(DETAIL_URL.format(job_id=job_id))
            return parse_job_detail(resp.text)
        except Exception as exc:  # detalhe é opcional; não derruba a busca
            log.debug("falha no detalhe %s: %s", job_id, exc)
            return ""
