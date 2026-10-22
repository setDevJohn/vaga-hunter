"""Fonte: GeekHunter — board 100% tech (sem login).

A listagem /pt/vagas é server-rendered (Next.js App Router) e aceita
?searchTerm=, ?page= e ?orderBy=moreRecent. Com termo de busca o default
é relevância, então orderBy=moreRecent é obrigatório para garantir a
ordem por data de publicação (decrescente).
Os cards trazem título, URL e data relativa ("Publicada há N horas");
empresa, local, modalidade e descrição vêm do JSON-LD JobPosting na
página de detalhe (buscada só para vaga nova dentro da janela).

Cloudflare na frente, hoje sem desafio (tolerou burst no recon de
2026-10-22). Se um dia ativarem challenge (403), a sessão trata como
bloqueio imediato (breaker no primeiro 403 + cooldown) — desafio de WAF
não se contorna, se respeita.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..http_client import PoliteSession, SourceBlockedError
from ..models import Job, SearchFilters
from .base import JobSource
from .infojobs import time_range_to_timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://www.geekhunter.com"
SEARCH_URL = f"{BASE_URL}/pt/vagas"

# Cloudflare: um eventual desafio responde 403 — bloqueio, não erro transitório
BLOCK_STATUS = {403, 429, 999}

_JOB_HREF_RE = re.compile(r"/pt/([^/\"]+)/jobs/([^/\"?#]+)")

# cards mostram "Publicada há X" ou "Atualizada há X" (ordenação moreRecent
# considera a atualização, como a Catho)
_RELATIVE_RE = re.compile(
    r"(?:Publicada|Atualizada)\s+há\s+(?:(\d+)\s+)?(minuto|hora|dia|semana|m[eê]s)",
    re.I)

_BRT = timezone(timedelta(hours=-3))

# granularidade fina (relativa ao agora) vs grossa (dia: meio-dia BRT,
# convenção do projeto para datas relativas — ver CLAUDE.md)
_FINE_DELTAS = {"minuto": timedelta(minutes=1), "hora": timedelta(hours=1)}
_DAY_DELTAS = {"dia": 1, "semana": 7, "mes": 30, "mês": 30}


def build_params(filters: SearchFilters, page: int = 1) -> dict[str, str]:
    # com searchTerm o default é relevância: orderBy garante ordem por data
    params = {"searchTerm": filters.keywords.strip(), "orderBy": "moreRecent"}
    if page > 1:
        params["page"] = str(page)
    return params


def parse_relative_date(text: str, now: datetime | None = None) -> datetime | None:
    """'Publicada há 3 horas' | 'Atualizada há 1 dia' -> datetime aproximado."""
    match = _RELATIVE_RE.search(text)
    if not match:
        return None
    count = int(match.group(1) or 1)
    unit = match.group(2).lower().replace("meses", "mês")
    now = now or datetime.now(timezone.utc)
    if unit in _FINE_DELTAS:
        return now - count * _FINE_DELTAS[unit]
    days = _DAY_DELTAS.get(unit)
    if days is None:
        return None
    noon = now.astimezone(_BRT).replace(hour=12, minute=0, second=0, microsecond=0)
    return noon - timedelta(days=count * days)


def _card_of(link) -> object:
    """Maior ancestral do link que ainda contém um único link de vaga.

    As classes CSS do site são hasheadas (instáveis); subir até a fronteira
    do card é o jeito robusto de isolar a data/badges da vaga certa.
    """
    node = link
    while node.parent is not None:
        links = [a for a in node.parent.find_all("a", href=True)
                 if _JOB_HREF_RE.search(a["href"])]
        if len(links) > 1:
            break
        node = node.parent
    return node


def parse_search_page(html: str, now: datetime | None = None) -> list[Job]:
    """Cards da listagem: título, URL e data relativa (resto vem do detalhe)."""
    soup = BeautifulSoup(html, "lxml")
    now = now or datetime.now(timezone.utc)
    jobs: list[Job] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="/jobs/"]'):
        href = link.get("href") or ""
        match = _JOB_HREF_RE.search(href)
        if not match:
            continue
        title = link.get_text(strip=True)
        if not title:
            continue
        company_slug, job_slug = match.groups()
        external_id = f"{company_slug}/{job_slug}"
        if external_id in seen:
            continue  # cards duplicados (render desktop + mobile)
        seen.add(external_id)

        card_text = _card_of(link).get_text(" ", strip=True)
        posted_at = parse_relative_date(card_text, now)

        jobs.append(Job(
            source="geekhunter",
            external_id=external_id,
            title=title,
            company=company_slug.replace("-", " ").title(),
            url=f"{BASE_URL}/pt/{company_slug}/jobs/{job_slug}",
            posted_at=posted_at,
        ))
    return jobs


def parse_job_detail(html: str, job: Job) -> Job:
    """Enriquece o card com o JSON-LD JobPosting da página de detalhe."""
    soup = BeautifulSoup(html, "lxml")
    posting = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            posting = data
            break
    if posting is None:
        return job

    description = str(posting.get("description") or "")
    if description:
        description = BeautifulSoup(description, "lxml").get_text(" ", strip=True)
    skills = str(posting.get("skills") or "")
    if skills:
        description = f"{description}\n\nSkills: {skills}".strip()

    org = posting.get("hiringOrganization") or {}
    company = str(org.get("name") or "") if isinstance(org, dict) else ""

    location = ""
    places = posting.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    if places and isinstance(places[0], dict):
        address = places[0].get("address") or {}
        if isinstance(address, dict):
            location = str(address.get("addressLocality") or "")

    remote = True if str(posting.get("jobLocationType") or "") == "TELECOMMUTE" else None
    if remote and not location:
        location = "Remoto"

    posted_at = job.posted_at
    if posted_at is None:
        date_posted = str(posting.get("datePosted") or "")
        try:
            posted_at = datetime.fromisoformat(date_posted)
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
        except ValueError:
            posted_at = None

    return Job(
        source=job.source,
        external_id=job.external_id,
        title=str(posting.get("title") or job.title).strip(),
        company=(company or job.company).strip(),
        location=location,
        url=job.url,
        description=description,
        posted_at=posted_at,
        remote=remote,
    )


class GeekhunterSource(JobSource):
    name = "geekhunter"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)
        self._yielded: set[str] = set()

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        for page in range(1, filters.max_pages + 1):
            resp = self.session.get(SEARCH_URL, params=build_params(filters, page))
            jobs = parse_search_page(resp.text)
            if not jobs:
                if page == 1 and resp.text.strip():
                    log.warning("GeekHunter: página não-vazia com 0 vagas parseadas "
                                "— formato pode ter mudado")
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
                if self.skip_detail(job.uid):
                    self._yielded.add(job.uid)
                    yield job
                    continue
                # detalhe antes de marcar: SourceBlockedError no meio não
                # queima a vaga para um run futuro dentro do mesmo processo
                job = self._fetch_job(job)
                self._yielded.add(job.uid)
                yield job
            # listagem ordenada por data desc: página inteira fora da janela
            # (ou toda duplicada) encerra a query
            if unseen == 0 or stale == unseen:
                break

    def _fetch_job(self, job: Job) -> Job:
        try:
            resp = self.session.get(job.url)
            return parse_job_detail(resp.text, job)
        except SourceBlockedError:
            raise
        except Exception as exc:
            log.debug("GeekHunter: falha no detalhe %s: %s", job.url, exc)
            return job
