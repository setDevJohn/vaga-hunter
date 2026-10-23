"""Fonte: APinfo — board de TI brasileiro veterano (sem login).

A busca é um POST para /apinfo/inc/list4.cfm (ColdFusion, iso-8859-1):
`keyw` + `onde=1` (só título) + `ddmmaa1/ddmmaa2` (filtro de PERÍODO
server-side, dd/mm/aa). A listagem vem ordenada por data decrescente e
cada card traz descrição completa, empresa e código — nenhum request de
detalhe é necessário. A paginação exige o `pkey` devolvido no form da
própria resposta ("Página X de Y").

O site não tem página pública por vaga (o link de candidatura usa pkey
de sessão): a URL do Job aponta para a busca e o código da vaga fica na
descrição — no site, buscar pelo código abre a vaga.

Sem CDN/WAF (Apache direto); tolerância normal de requisições.
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
from .infojobs import time_range_to_timedelta

log = logging.getLogger(__name__)

BASE_URL = "https://www.apinfo.com"
SEARCH_URL = f"{BASE_URL}/apinfo/inc/list4.cfm"

_BRT = timezone(timedelta(hours=-3))

_INFO_RE = re.compile(r"^(.*?)\s*-\s*(\d{2}/\d{2}/\d{2})\s*$", re.S)
_PAGES_RE = re.compile(r"Página\s+\d+\s+de\s+(\d+)")
_PKEY_RE = re.compile(r'<input[^>]*name="pkey"[^>]*value="([A-Za-z0-9]+)"')
_TCV_RE = re.compile(r'<input[^>]*name="tcv"[^>]*value="(\d+)"')
_CODE_RE = re.compile(r"Código\s*\.*\s*:?\s*(\d+)")
_COMPANY_RE = re.compile(r"Empresa\s*\.*\s*:?\s*(.+)")


def build_form(filters: SearchFilters, page: int = 1,
               pkey: str = "", tcv: str = "1",
               now: datetime | None = None) -> dict[str, str]:
    """Form do POST; ddmmaa1 pede ao servidor só o período desejado."""
    cutoff = (now or datetime.now(_BRT)).astimezone(_BRT) \
        - time_range_to_timedelta(filters.time_range)
    form = {
        "tcv": tcv,
        "pag": str(page),
        "ddmmaa1": cutoff.strftime("%d/%m/%y"),
        "ddmmaa2": "",
        "keyw": filters.keywords.strip(),
        "onde": "1",       # só no título (menos ruído)
        "andor": "1",      # todas as palavras
    }
    if pkey:
        form["pkey"] = pkey
    return form


def parse_date(text: str) -> datetime | None:
    """'23/10/26' -> meio-dia BRT (convenção do projeto p/ data sem hora)."""
    try:
        parsed = datetime.strptime(text.strip(), "%d/%m/%y")
    except ValueError:
        return None
    return parsed.replace(hour=12, tzinfo=_BRT)


def parse_search_page(html: str) -> tuple[list[Job], int, str, str]:
    """Retorna (vagas, total de páginas, pkey, tcv) da resposta."""
    total_pages = 1
    match = _PAGES_RE.search(html)
    if match:
        total_pages = int(match.group(1))
    pkey_match = _PKEY_RE.search(html)
    pkey = pkey_match.group(1) if pkey_match else ""
    if not pkey and total_pages > 1:
        log.warning("APinfo: pkey não encontrado com %d páginas — "
                    "paginação indisponível (formato pode ter mudado)",
                    total_pages)
    # a página tem dois forms com tcv (busca=1, paginação=real): usar o último
    tcv_values = _TCV_RE.findall(html)
    tcv = tcv_values[-1] if tcv_values else "1"

    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select("div.box-vagas"):
        info = card.select_one("div.info-data")
        title_el = card.select_one("div.cargo")
        text_el = card.select_one("div.texto")
        if not (info and title_el and text_el):
            continue
        description = text_el.get_text("\n", strip=True)
        code_match = _CODE_RE.search(description)
        if not code_match:
            continue  # sem código não há como deduplicar
        external_id = code_match.group(1)
        if external_id in seen:
            continue
        seen.add(external_id)

        location = ""
        posted_at = None
        info_match = _INFO_RE.match(info.get_text(" ", strip=True))
        if info_match:
            location = info_match.group(1).strip()
            posted_at = parse_date(info_match.group(2))
        remote = True if "home office" in location.lower() else None
        if remote:
            location = "Remoto"

        company = ""
        company_match = _COMPANY_RE.search(description)
        if company_match:
            company = company_match.group(1).strip()

        jobs.append(Job(
            source="apinfo",
            external_id=external_id,
            title=title_el.get_text(" ", strip=True),
            company=company,
            location=location,
            # sem página pública por vaga: a URL abre a busca e o código
            # (na descrição) localiza a vaga no site
            url=f"{SEARCH_URL}#vaga-{external_id}",
            description=description,
            posted_at=posted_at,
            remote=remote,
        ))
    return jobs, total_pages, pkey, tcv


class ApinfoSource(JobSource):
    name = "apinfo"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)  # descrição já vem no card
        self._yielded: set[str] = set()

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        pkey = ""
        tcv = "1"
        total_pages = 1
        for page in range(1, filters.max_pages + 1):
            if page > total_pages:
                break
            resp = self.session.post(
                SEARCH_URL, data=build_form(filters, page, pkey, tcv))
            html = resp.content.decode("iso-8859-1", "replace")
            jobs, total_pages, pkey, tcv = parse_search_page(html)
            if not jobs:
                if page == 1 and html.strip():
                    log.warning("APinfo: página não-vazia com 0 vagas parseadas "
                                "— formato pode ter mudado")
                break
            stale = unseen = 0
            for job in jobs:
                if job.uid in self._yielded:
                    continue
                unseen += 1
                # ddmmaa1 já filtra no servidor por dia; o corte local só
                # apara a borda (vaga do dia do cutoff, horas antes dele)
                if job.posted_at is not None and job.posted_at < cutoff:
                    stale += 1
                    continue  # fora da janela: não marca o dedup
                self._yielded.add(job.uid)
                yield job
            # listagem ordenada por data desc: página toda fora da janela
            # (ou toda duplicada) encerra a query
            if unseen == 0 or stale == unseen:
                break
