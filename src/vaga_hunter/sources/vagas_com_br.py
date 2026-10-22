"""Fonte: busca pública de vagas do Vagas.com.br (sem login).

Server-rendered: /vagas-de-<termo>?ordenar_por=mais_recentes&pagina=N
(40 vagas/página). Cards trazem nível de senioridade explícito
(.nivelVaga) e o detalhe tem JSON-LD JobPosting com datePosted.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from ._jsonld import extract_jobposting, jobposting_description
from .base import JobSource
from .infojobs import time_range_to_timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://www.vagas.com.br"
_BRT = timezone(timedelta(hours=-3))


def _fold(text: str) -> str:
    """minúsculas + sem acentos, p/ comparações ('Sênior' ~ 'SENIOR')."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def query_slug(keywords: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(keywords)).strip("-") or "ti"


def build_url(filters: SearchFilters, page: int = 1) -> str:
    url = f"{BASE_URL}/vagas-de-{query_slug(filters.keywords)}?ordenar_por=mais_recentes"
    if page > 1:
        url += f"&pagina={page}"
    return url


def parse_posted_text(text: str, now: datetime | None = None) -> datetime | None:
    """'Hoje' | 'Ontem' | 'Há N dias' | 'dd/mm/yyyy'."""
    now = (now or datetime.now(_BRT)).astimezone(_BRT)
    text = text.strip().lower()
    base = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if "hoje" in text:
        return base
    if "ontem" in text:
        return base - timedelta(days=1)
    match = re.search(r"h[áa]\s+(\d+)\s+dia", text)
    if match:
        return base - timedelta(days=int(match.group(1)))
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        try:
            return datetime(int(match.group(3)), int(match.group(2)),
                            int(match.group(1)), 12, tzinfo=_BRT)
        except ValueError:
            return None
    return None


def parse_search_page(html: str, now: datetime | None = None) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select("li.vaga"):
        link = card.select_one("a.link-detalhes-vaga[data-id-vaga]")
        if link is None:
            continue
        job_id = link.get("data-id-vaga", "").strip()
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)

        title = " ".join(link.get_text(" ", strip=True).split())
        level_el = card.select_one(".nivelVaga")
        if level_el:
            level = level_el.get_text(strip=True)
            # nível explícito do site vai pro título: o scoring de senioridade
            # lê o título, e muitos títulos daqui não trazem o nível
            if level and _fold(level) not in _fold(title):
                title = f"{title} ({level})"

        company_el = card.select_one(".emprVaga")
        location_el = card.select_one(".vaga-local")
        date_el = card.select_one(".data-publicacao")
        snippet_el = card.select_one(".detalhes")

        posted_at = None
        if date_el:
            posted_at = parse_posted_text(date_el.get_text(" ", strip=True), now)

        href = link.get("href", "")
        jobs.append(Job(
            source="vagas_com_br",
            external_id=job_id,
            title=title,
            company=company_el.get_text(strip=True) if company_el else "",
            location=location_el.get_text(" ", strip=True) if location_el else "",
            url=f"{BASE_URL}{href}" if href.startswith("/") else href,
            description=snippet_el.get_text(" ", strip=True) if snippet_el else "",
            posted_at=posted_at,
        ))
    return jobs


def parse_job_detail(html: str) -> tuple[str, datetime | None]:
    """(descrição, datePosted) do JSON-LD JobPosting."""
    posting = extract_jobposting(html)
    description = jobposting_description(html)
    posted_at = None
    date_raw = posting.get("datePosted")
    if date_raw:
        try:
            posted_at = datetime.fromisoformat(str(date_raw)).replace(
                hour=12, tzinfo=_BRT)
        except ValueError:
            pass
    return description, posted_at


class VagasComBrSource(JobSource):
    name = "vagas_com_br"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)
        self._yielded: set[str] = set()

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        self._yielded.clear()
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        for page in range(1, filters.max_pages + 1):
            resp = self.session.get(build_url(filters, page))
            jobs = parse_search_page(resp.text)
            if not jobs:
                if page == 1 and resp.text.strip():
                    log.warning("Vagas.com.br: página não-vazia com 0 vagas "
                                "parseadas — markup pode ter mudado")
                break
            stale = 0
            unseen = 0
            for job in jobs:
                if job.uid in self._yielded:
                    continue  # páginas vizinhas se sobrepõem um pouco
                self._yielded.add(job.uid)
                unseen += 1
                if job.posted_at is not None and job.posted_at < cutoff:
                    stale += 1
                    continue
                if not self.skip_detail(job.uid):
                    description, posted_at = self._fetch_detail(job.url)
                    if description:
                        job.description = description
                    if posted_at is not None:
                        job.posted_at = posted_at
                yield job
            if unseen == 0 or stale == unseen:
                break

    def _fetch_detail(self, url: str) -> tuple[str, datetime | None]:
        from ..http_client import SourceBlockedError
        try:
            resp = self.session.get(url)
            return parse_job_detail(resp.text)
        except SourceBlockedError:
            raise
        except Exception as exc:
            log.debug("Vagas.com.br: falha no detalhe %s: %s", url, exc)
            return "", None
