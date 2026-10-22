"""Fonte: Programathor — board 100% tech (sem login).

Cards trazem stack em tags, nível e modalidade explícitos. O site não
expõe data de publicação (recência fica neutra no scoring). Algumas
páginas de detalhe retornam 500 (bug do próprio site) — o card já traz
informação suficiente como fallback.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator

from bs4 import BeautifulSoup

from ..scoring import _normalize as _fold

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from .base import JobSource

log = logging.getLogger(__name__)

BASE_URL = "https://programathor.com.br"
SEARCH_URL = f"{BASE_URL}/jobs"


def build_params(filters: SearchFilters, page: int = 1) -> dict[str, str]:
    params = {"search": filters.keywords.strip()}
    if page > 1:
        params["page"] = str(page)
    return params


def parse_search_page(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select("div.cell-list"):
        link = card.select_one('a[href^="/jobs/"]')
        if link is None:
            continue
        match = re.match(r"/jobs/(\d+)", link.get("href", ""))
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)

        title_el = card.select_one("h3")
        title = title_el.get_text(strip=True) if title_el else ""
        title = re.sub(r"NOVA$", "", title).strip()  # badge "NOVA" gruda no h3

        company = location = level = contract = ""
        for span in card.select(".cell-list-content-icon > span"):
            icon = span.select_one("i")
            icon_classes = " ".join(icon.get("class", [])) if icon else ""
            text = span.get_text(" ", strip=True)
            if "fa-briefcase" in icon_classes:
                company = text
            elif "fa-map-marker" in icon_classes:
                location = text
            elif "fa-chart-bar" in icon_classes:
                level = text
            elif "fa-file-alt" in icon_classes:
                contract = text

        if level and _fold(level) not in _fold(title):
            title = f"{title} ({level})"  # nível explícito ajuda o scoring

        tags = [t.get_text(strip=True) for t in card.select(".tag-list")]
        parts = []
        if tags:
            parts.append("Stack: " + ", ".join(tags))
        if contract:
            parts.append(f"Contrato: {contract}")
        if location:
            parts.append(f"Local: {location}")

        jobs.append(Job(
            source="programathor",
            external_id=job_id,
            title=title,
            company=company,
            location=location,
            url=f"{BASE_URL}{link['href']}",
            description="\n".join(parts),
            posted_at=None,  # site não expõe data de publicação
        ))
    return jobs


def parse_job_detail(html: str) -> str:
    """Descrição: parágrafos do corpo principal da vaga."""
    soup = BeautifulSoup(html, "lxml")
    containers = soup.select(".container") or ([soup.body] if soup.body else [])
    container = max(containers, default=None,
                    key=lambda c: len(c.get_text(strip=True)))
    if container is None:
        return ""
    for tag in container.select("script, style, form, nav"):
        tag.extract()
    paragraphs = container.find_all("p")
    text = "\n".join(p.get_text(" ", strip=True) for p in paragraphs
                     if p.get_text(strip=True))
    if len(text) > 100:
        return text
    meta = soup.select_one('meta[property="og:description"]')
    return meta.get("content", "") if meta else text


class ProgramathorSource(JobSource):
    name = "programathor"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)
        self._yielded: set[str] = set()

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        # Sem datas no site: time_range não se aplica; busca limitada por
        # max_pages e pelo dedup do banco.
        self._yielded.clear()
        for page in range(1, filters.max_pages + 1):
            resp = self.session.get(SEARCH_URL, params=build_params(filters, page))
            jobs = parse_search_page(resp.text)
            if not jobs:
                if page == 1 and resp.text.strip():
                    log.warning("Programathor: página não-vazia com 0 vagas "
                                "parseadas — markup pode ter mudado")
                break
            unseen = 0
            for job in jobs:
                if job.uid in self._yielded:
                    continue
                self._yielded.add(job.uid)
                unseen += 1
                if not self.skip_detail(job.uid):
                    detail = self._fetch_description(job.url)
                    if detail:
                        # mantém as tags do card no topo (stack explícita)
                        job.description = f"{job.description}\n\n{detail}".strip()
                yield job
            if unseen == 0:
                break

    def _fetch_description(self, url: str) -> str:
        from ..http_client import SourceBlockedError
        try:
            resp = self.session.get(url)
            return parse_job_detail(resp.text)
        except SourceBlockedError:
            raise
        except Exception as exc:  # detalhes 500 são comuns no site
            log.debug("Programathor: falha no detalhe %s: %s", url, exc)
            return ""
