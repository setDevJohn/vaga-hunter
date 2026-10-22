"""Fonte: Empregos.com.br (Solides) — busca pública por cargo (sem login).

A página /vagas/<cargo> é server-rendered (Nuxt 3) e embute as vagas no
payload __NUXT_DATA__ (formato achatado: valores são índices para outras
posições da lista). A descrição completa já vem no payload — nenhum request
de detalhe é necessário.

Limitações conhecidas:
- Só a primeira página por query (~20 vagas): parâmetros de paginação e
  ordenação disparam o WAF do site (403 "Verificação de segurança").
- A listagem NÃO é ordenada por data: o corte por período filtra item a
  item, sem encerrar cedo.
- Data relativa ("há N dias") — granularidade de dia.
O WAF (GoCache) pune bursts e bloqueia por reputação de IP com desafio
reCAPTCHA; a sessão desta fonte trata 403 como bloqueio imediato
(breaker dispara no primeiro 403 + cooldown) — retry só piora o bloqueio.
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

BASE_URL = "https://www.empregos.com.br"
_BRT = timezone(timedelta(hours=-3))

# 403 "Verificação de segurança" = WAF: conta para o circuit breaker
BLOCK_STATUS = {403, 429, 999}

_NUXT_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)

_JOB_KEYS = {"referenceId", "title", "description", "workplaceTypes"}


def query_slug(keywords: str) -> str:
    text = unicodedata.normalize("NFKD", keywords.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "vagas"


def build_url(filters: SearchFilters) -> str:
    return f"{BASE_URL}/vagas/{query_slug(filters.keywords)}"


def parse_created_at(text: str, now: datetime | None = None) -> datetime | None:
    """'30 dias' | '1 dia' | 'hoje' | 'ontem' -> datetime aproximado (12h BRT)."""
    now = (now or datetime.now(_BRT)).astimezone(_BRT)
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    text = text.strip().lower()
    if "hoje" in text or "hora" in text or "minuto" in text:
        return noon
    if "ontem" in text:
        return noon - timedelta(days=1)
    match = re.search(r"(\d+)\s*dia", text)
    if match:
        return noon - timedelta(days=int(match.group(1)))
    return None


def _resolve(data: list, value, depth: int = 0):
    """Resolve o formato achatado do Nuxt 3: ints são índices em `data`."""
    if depth > 8:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(data):
            return _resolve(data, data[value], depth + 1)
        return value
    if isinstance(value, dict):
        return {k: _resolve(data, v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(data, v, depth + 1) for v in value]
    return value


def parse_search_page(html: str, now: datetime | None = None) -> list[Job]:
    match = _NUXT_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []
    if not isinstance(data, list):
        return []

    jobs: list[Job] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or not _JOB_KEYS <= set(item.keys()):
            continue
        job = _resolve(data, item)
        external_id = str(job.get("referenceId") or "")
        if not external_id or external_id in seen:
            continue
        seen.add(external_id)

        description = str(job.get("description") or "")
        if description:
            description = BeautifulSoup(description, "lxml").get_text(" ", strip=True)

        workplace = job.get("workplaceTypes") or ""
        if isinstance(workplace, list):
            workplace = ", ".join(str(w) for w in workplace)
        workplace = str(workplace)
        remote = True if "remoto" in workplace.lower() else None

        location = ""
        loc = job.get("location")
        if isinstance(loc, list) and loc:
            loc = loc[0]
        if isinstance(loc, dict):
            parts = [loc.get("city") or loc.get("name"), loc.get("state") or loc.get("uf")]
            location = ", ".join(str(p) for p in parts if p)
        elif isinstance(loc, str):
            location = loc
        if remote and not location:
            location = "Remoto"

        company = job.get("company")
        company_name = ""
        if isinstance(company, dict):
            company_name = str(company.get("name") or "")

        posted_at = None
        created = job.get("createdAtFormat")
        if isinstance(created, str):
            posted_at = parse_created_at(created, now)

        if workplace and workplace.lower() not in description.lower():
            description = f"{description}\n\nModalidade: {workplace}".strip()

        jobs.append(Job(
            source="empregos",
            external_id=external_id,
            title=str(job.get("title") or "").strip(),
            company=company_name.strip(),
            location=location,
            url=f"{BASE_URL}/vaga/{external_id}",
            description=description,
            posted_at=posted_at,
            remote=remote,
        ))
    return jobs


class EmpregosSource(JobSource):
    name = "empregos"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)  # descrição já vem na página
        self._yielded: set[str] = set()

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        # WAF bloqueia parâmetros de paginação: só a 1ª página por query.
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        resp = self.session.get(build_url(filters))
        jobs = parse_search_page(resp.text)
        if not jobs and resp.text.strip():
            log.warning("Empregos.com.br: página não-vazia com 0 vagas parseadas "
                        "— payload pode ter mudado")
        for job in jobs:
            if job.uid in self._yielded:
                continue
            # listagem não é ordenada por data: filtra sem encerrar cedo;
            # vaga fora da janela não marca o dedup (outra query com janela
            # maior ainda pode entregá-la)
            if job.posted_at is not None and job.posted_at < cutoff:
                continue
            self._yielded.add(job.uid)
            yield job
