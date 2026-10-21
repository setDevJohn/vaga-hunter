"""Fonte: busca pública de vagas da Catho (sem login).

Server-rendered: query vira slug no path (/vagas/<slug>/), ordenação por
recência via ?order=dataAtualizacao, paginação ?page=N. O detalhe traz
JSON-LD JobPosting com descrição completa e datePosted.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from .base import JobSource
from .infojobs import time_range_to_timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://www.catho.com.br"
_BRT = timezone(timedelta(hours=-3))


def query_slug(keywords: str) -> str:
    text = unicodedata.normalize("NFKD", keywords.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def build_url(filters: SearchFilters, page: int = 1) -> str:
    url = f"{BASE_URL}/vagas/{query_slug(filters.keywords)}/?order=dataAtualizacao"
    if page > 1:
        url += f"&page={page}"
    return url


def parse_posted_tag(text: str, now: datetime | None = None) -> datetime | None:
    """'Publicada Hoje' | 'Publicada Ontem' | 'Publicada em 16/10' (sem ano)."""
    now = (now or datetime.now(_BRT)).astimezone(_BRT)
    text = text.strip().lower()
    if "hoje" in text:
        return now.replace(hour=12, minute=0, second=0, microsecond=0)
    if "ontem" in text:
        return (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    match = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    try:
        posted = now.replace(month=month, day=day, hour=12, minute=0,
                             second=0, microsecond=0)
    except ValueError:
        return None
    if posted > now:  # mês futuro = era do ano passado
        posted = posted.replace(year=posted.year - 1)
    return posted


def parse_search_page(html: str, now: datetime | None = None) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select("li[data-offer-item]"):
        job_id = card.get("data-offer-item", "").strip()
        link = card.select_one("h2.title_offer a[href]")
        if not job_id or link is None or job_id in seen:
            continue
        seen.add(job_id)

        company = ""
        company_el = card.select_one("p .text-12")
        if company_el:
            company = company_el.get_text(strip=True)
        elif card.get("data-blind") == "true":
            company = "Confidencial"

        location = ""
        location_icon = card.select_one(".i_job_location")
        if location_icon and location_icon.parent:
            text = location_icon.parent.get_text(" ", strip=True)
            # "1 vaga - Contagem" -> "Contagem"
            location = text.split("-", 1)[1].strip() if "-" in text else text

        posted_at = None
        tag = card.find("span", class_=re.compile(r"\bpub_"))
        if tag:
            posted_at = parse_posted_tag(tag.get_text(strip=True), now)

        salary = ""
        salary_icon = card.select_one(".i_salary")
        if salary_icon and salary_icon.parent:
            salary = salary_icon.parent.get_text(" ", strip=True)

        href = link["href"]
        jobs.append(Job(
            source="catho",
            external_id=job_id,
            title=link.get_text(strip=True) or link.get("title", ""),
            company=company,
            location=location,
            url=f"{BASE_URL}{href}" if href.startswith("/") else href,
            description=salary,  # placeholder até o detalhe; salário é útil no score de keywords? mantém informativo
            posted_at=posted_at,
        ))
    return jobs


def parse_job_detail(html: str) -> str:
    """Descrição do JSON-LD JobPosting; fallback no og:description."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                description = item.get("description", "")
                if description:
                    text = BeautifulSoup(description, "lxml").get_text(" ", strip=True)
                    return text
    meta = soup.select_one('meta[property="og:description"]')
    return meta.get("content", "") if meta else ""


class CathoSource(JobSource):
    name = "catho"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        for page in range(1, filters.max_pages + 1):
            resp = self.session.get(build_url(filters, page))
            jobs = parse_search_page(resp.text)
            if not jobs:
                if page == 1 and resp.text.strip():
                    log.warning("Catho: página não-vazia com 0 vagas parseadas — "
                                "markup pode ter mudado")
                break
            stale = 0
            for job in jobs:
                # corte client-side por período; patrocinadas antigas ficam
                # pinadas no topo, então 'stale' só encerra se a página INTEIRA
                # estiver fora da janela
                if job.posted_at is not None and job.posted_at < cutoff:
                    stale += 1
                    continue
                if not self.skip_detail(job.uid):
                    description = self._fetch_description(job.url)
                    if description:
                        job.description = description
                yield job
            if stale == len(jobs):
                break

    def _fetch_description(self, url: str) -> str:
        from ..http_client import SourceBlockedError
        try:
            resp = self.session.get(url)
            return parse_job_detail(resp.text)
        except SourceBlockedError:
            raise
        except Exception as exc:
            log.debug("Catho: falha no detalhe %s: %s", url, exc)
            return ""
