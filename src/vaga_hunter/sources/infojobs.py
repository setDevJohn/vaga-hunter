"""Fonte: busca pública de vagas do InfoJobs Brasil (sem login).

Site server-rendered, sem bloqueio com UA de navegador. Cards trazem
timestamp exato de publicação (js_date[data-value]), salário, modalidade
e um resumo da descrição; o detalhe completa a descrição.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from .base import JobSource
from .linkedin_jobs import TIME_RANGE_RE, TIME_RANGES, _UNIT_SECONDS

log = logging.getLogger(__name__)

BASE_URL = "https://www.infojobs.com.br"
SEARCH_URL = f"{BASE_URL}/empregos.aspx"
# Versão de impressão da vaga: leve (~13KB) e com a descrição completa —
# a página normal de detalhe só traz o resumo truncado (resto vem via XHR).
PRINT_URL = f"{BASE_URL}/vacancydetail/print?iv={{job_id}}"

# fuso de referência das datas do site (horário de Brasília)
_BRT = timezone(timedelta(hours=-3))


def build_params(filters: SearchFilters, page: int = 1) -> dict[str, str]:
    """Query string da busca. InfoJobs não filtra período na URL — o corte
    por time_range é feito client-side em `search()`."""
    palabra = filters.keywords.strip().strip('"')
    location = (filters.location or "").strip()
    # Sem filtro estruturado de local confiável na URL: cidade/UF entram na
    # palavra-chave; "Brasil" (site é só BR) não acrescenta nada.
    if location and not re.search(r"^brasil$|^brazil$", location, re.IGNORECASE):
        palabra = f"{palabra} {location}"
    params = {"palabra": palabra, "sortby": "PublishedDate"}
    if page > 1:
        params["Page"] = str(page)
    return params


def time_range_to_timedelta(value: str) -> timedelta:
    """Converte o time_range (mesma sintaxe do LinkedIn) em timedelta."""
    seconds_map = {k: int(v[1:]) for k, v in TIME_RANGES.items()}
    if value in seconds_map:
        return timedelta(seconds=seconds_map[value])
    match = TIME_RANGE_RE.match(value or "")
    if match:
        seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
        if seconds > 0:
            return timedelta(seconds=seconds)
    return timedelta(days=7)


def parse_posted_at(value: str) -> datetime | None:
    """`js_date[data-value]` vem como '2026/10/21 01:28:00' (hora local BR)."""
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M:%S").replace(tzinfo=_BRT)
    except ValueError:
        return None


def parse_search_page(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select("div.js_vacancyLoad[data-id]"):
        job_id = card.get("data-id", "").strip()
        href = card.get("data-href", "")
        if not job_id or not href or job_id in seen:
            continue  # a página repete alguns cards (variantes de layout)
        seen.add(job_id)

        title_el = card.select_one("h2.js_vacancyTitle")
        date_el = card.select_one("div.js_date[data-value]")

        company = ""
        company_el = card.select_one('a[href*="/empresa-"]')
        if company_el:
            company = " ".join(company_el.get_text(" ", strip=True).split())
            # remove texto do tooltip do selo de verificação, se grudar
            company = re.sub(r"\s*Este selo indica.*$", "", company)
        else:
            # vagas confidenciais não têm link de empresa
            confidential = card.find(string=re.compile("Confidencial", re.I))
            if confidential:
                company = "Confidencial"

        location = ""
        location_el = card.select_one("div.mb-8")
        if location_el:
            hidden = location_el.select_one(".js_divUserVagaDistance")
            if hidden:
                hidden.extract()  # "..., 0 Km de você."
            location = location_el.get_text(" ", strip=True)

        # resumo da descrição no próprio card (bloco .text-medium após os ícones)
        snippet = ""
        snippet_el = card.select("div.text-medium")
        if snippet_el:
            snippet = snippet_el[-1].get_text(" ", strip=True)

        jobs.append(Job(
            source="infojobs",
            external_id=job_id,
            title=title_el.get_text(strip=True) if title_el else "",
            company=company,
            location=location,
            url=f"{BASE_URL}{href}" if href.startswith("/") else href,
            description=snippet,
            posted_at=parse_posted_at(date_el["data-value"]) if date_el else None,
        ))
    return jobs


def parse_job_detail(html: str) -> str:
    """Descrição completa a partir da versão de impressão da vaga."""
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one("div.container") or soup.body
    if container is None:
        return ""
    for tag in container.select("script, style, header, nav"):
        tag.extract()
    text = container.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


class InfoJobsSource(JobSource):
    name = "infojobs"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        for page in range(1, filters.max_pages + 1):
            resp = self.session.get(SEARCH_URL, params=build_params(filters, page))
            jobs = parse_search_page(resp.text)
            if not jobs:
                if page == 1 and resp.text.strip():
                    log.warning("InfoJobs: página não-vazia com 0 vagas parseadas — "
                                "markup pode ter mudado")
                break
            stale = 0
            for job in jobs:
                # corte client-side por período (site não filtra na URL);
                # resultados vêm por data: velhas demais = fim da janela útil
                if job.posted_at is not None and job.posted_at < cutoff:
                    stale += 1
                    continue
                if not self.skip_detail(job.uid):
                    description = self._fetch_description(job.external_id)
                    if description:
                        job.description = description
                yield job
            if stale == len(jobs):
                break  # página inteira fora do período — não há mais recentes

    def _fetch_description(self, job_id: str) -> str:
        from ..http_client import SourceBlockedError
        try:
            resp = self.session.get(PRINT_URL.format(job_id=job_id))
            return parse_job_detail(resp.text)
        except SourceBlockedError:
            raise
        except Exception as exc:
            log.debug("InfoJobs: falha no detalhe %s: %s", url, exc)
            return ""
