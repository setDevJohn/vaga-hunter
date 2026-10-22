"""Fonte: Trampos.co — vagas de comunicação e tecnologia (sem login).

O site é uma SPA (Ember) com API fechada (401 sem sessão), mas publica um
sitemap com lastmod por vaga e cada página de vaga traz JSON-LD JobPosting
completo. Estratégia: 1 request no sitemap por execução, filtra por período
(lastmod) e por query (palavras no slug da URL), e só então busca o detalhe
das vagas que casaram e ainda não estão no banco.
"""
from __future__ import annotations

import gzip
import logging
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..http_client import PoliteSession
from ..models import Job, SearchFilters
from ._jsonld import extract_jobposting
from .base import JobSource
from .infojobs import time_range_to_timedelta

log = logging.getLogger(__name__)

SITEMAP_URL = "https://trampos.co/sitemaps/sitemap.xml.gz"
_BRT = timezone(timedelta(hours=-3))

_ENTRY_RE = re.compile(
    r"<loc>\s*(https?://(?:www\.)?trampos\.co/oportunidade/(\d+)-([a-z0-9-]+))\s*</loc>"
    r"\s*<lastmod>\s*([^<\s]+)\s*</lastmod>")

# palavras curtas de ligação que não contam no match query x slug
_STOPWORDS = {"de", "da", "do", "e", "em", "para", "a", "o"}


@dataclass
class SitemapEntry:
    external_id: str
    slug: str
    url: str
    lastmod: datetime


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def parse_sitemap(xml: str) -> list[SitemapEntry]:
    """Extrai as vagas (/oportunidade/<id>-<slug>) com lastmod do sitemap."""
    entries: list[SitemapEntry] = []
    seen: set[str] = set()
    for match in _ENTRY_RE.finditer(xml):
        url, external_id, slug, lastmod_raw = match.groups()
        if external_id in seen:
            continue
        seen.add(external_id)
        try:
            lastmod = datetime.fromisoformat(lastmod_raw)
        except ValueError:
            continue
        if lastmod.tzinfo is None:
            lastmod = lastmod.replace(tzinfo=_BRT)
        entries.append(SitemapEntry(external_id=external_id, slug=slug,
                                    url=url, lastmod=lastmod))
    return entries


def slug_matches(keywords: str, slug: str) -> bool:
    """Toda palavra significativa da query precisa aparecer no slug.

    Compara também a forma sem separadores, para "fullstack" casar com
    "full-stack" (e vice-versa).
    """
    tokens = [t for t in _fold(slug).split("-") if t]
    token_set = set(tokens)
    # pares adjacentes concatenados: slug "full-stack" atende query "fullstack"
    joined = {a + b for a, b in zip(tokens, tokens[1:])}
    words = [w for w in re.split(r"[^a-z0-9]+", _fold(keywords))
             if w and w not in _STOPWORDS]
    if not words:
        return False
    i = 0
    while i < len(words):
        if words[i] in token_set or words[i] in joined:
            i += 1
            continue
        # query "full stack" atende slug "fullstack"
        if i + 1 < len(words) and words[i] + words[i + 1] in token_set:
            i += 2
            continue
        return False
    return True


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").strip().capitalize()


def parse_job_detail(html: str, entry: SitemapEntry) -> Job:
    """Monta o Job a partir do JSON-LD JobPosting da página da vaga."""
    posting = extract_jobposting(html)
    title = str(posting.get("title") or "") or _title_from_slug(entry.slug)
    org = posting.get("hiringOrganization") or {}
    company = str(org.get("name", "")) if isinstance(org, dict) else ""

    description = posting.get("description") or ""
    if description:
        description = BeautifulSoup(str(description), "lxml").get_text(
            " ", strip=True)
    else:
        soup = BeautifulSoup(html, "lxml")
        meta = soup.select_one('meta[property="og:description"]')
        description = meta.get("content", "") if meta else ""

    posted_at = entry.lastmod
    date_posted = str(posting.get("datePosted") or "")
    if date_posted:
        try:
            posted_at = datetime.fromisoformat(date_posted)
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(hour=12, tzinfo=_BRT)
        except ValueError:
            posted_at = entry.lastmod

    location = ""
    place = posting.get("jobLocation") or {}
    if isinstance(place, list):
        place = place[0] if place else {}
    if isinstance(place, dict):
        address = place.get("address") or {}
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressRegion")]
            location = ", ".join(str(p) for p in parts if p)

    # o site serializa a chave em minúsculas ("joblocationtype")
    location_type = str(posting.get("jobLocationType")
                        or posting.get("joblocationtype") or "")
    remote = True if location_type.upper() == "TELECOMMUTE" else None
    if remote and not location:
        location = "Remoto"

    employment = str(posting.get("employmentType") or "")
    if employment:
        description = f"{description}\n\nContrato: {employment}".strip()

    return Job(
        source="trampos",
        external_id=entry.external_id,
        title=title,
        company=company,
        location=location,
        url=entry.url,
        description=description,
        posted_at=posted_at,
        remote=remote,
    )


class TramposSource(JobSource):
    name = "trampos"
    requires_auth = False

    def __init__(self, session: PoliteSession,
                 skip_detail: Callable[[str], bool] | None = None):
        self.session = session
        self.skip_detail = skip_detail or (lambda uid: False)
        self._entries: list[SitemapEntry] | None = None  # cache por execução
        self._yielded: set[str] = set()

    def _sitemap_entries(self) -> list[SitemapEntry]:
        if self._entries is None:
            resp = self.session.get(SITEMAP_URL)
            content = resp.content
            if content[:2] == b"\x1f\x8b":  # servido como .gz literal
                content = gzip.decompress(content)
            self._entries = parse_sitemap(content.decode("utf-8", "replace"))
            if not self._entries:
                log.warning("Trampos: sitemap sem vagas parseadas — "
                            "formato pode ter mudado")
        return self._entries

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        # max_pages não se aplica: o sitemap é único e já traz tudo.
        cutoff = datetime.now(timezone.utc) - time_range_to_timedelta(filters.time_range)
        for entry in self._sitemap_entries():
            if entry.lastmod < cutoff:
                continue
            if not slug_matches(filters.keywords, entry.slug):
                continue
            uid = f"trampos:{entry.external_id}"
            if uid in self._yielded:
                continue  # mesma vaga casando com outra query do run
            self._yielded.add(uid)
            if self.skip_detail(uid):
                yield Job(source="trampos", external_id=entry.external_id,
                          title=_title_from_slug(entry.slug), url=entry.url,
                          posted_at=entry.lastmod)
                continue
            yield self._fetch_job(entry)

    def _fetch_job(self, entry: SitemapEntry) -> Job:
        from ..http_client import SourceBlockedError
        try:
            resp = self.session.get(entry.url)
            return parse_job_detail(resp.text, entry)
        except SourceBlockedError:
            raise
        except Exception as exc:
            log.debug("Trampos: falha no detalhe %s: %s", entry.url, exc)
            return Job(source="trampos", external_id=entry.external_id,
                       title=_title_from_slug(entry.slug), url=entry.url,
                       posted_at=entry.lastmod)
