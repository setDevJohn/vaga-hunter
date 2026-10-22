"""Testes da fonte Empregos.com.br (payload __NUXT_DATA__)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.empregos import (
    EmpregosSource,
    build_url,
    parse_created_at,
    parse_search_page,
    query_slug,
)

FIXTURES = Path(__file__).parent / "fixtures"
_BRT = timezone(timedelta(hours=-3))


@pytest.fixture
def search_html() -> str:
    return (FIXTURES / "empregos_search.html").read_text()


class TestQuerySlug:
    def test_basico(self):
        assert query_slug("desenvolvedor web") == "desenvolvedor-web"
        assert query_slug("Analista de Sistemas") == "analista-de-sistemas"

    def test_acentos(self):
        assert query_slug("programação") == "programacao"

    def test_url(self):
        filters = SearchFilters(keywords="desenvolvedor")
        assert build_url(filters) == "https://www.empregos.com.br/vagas/desenvolvedor"


class TestParseCreatedAt:
    NOW = datetime(2026, 10, 22, 15, 0, tzinfo=_BRT)

    def test_dias(self):
        parsed = parse_created_at("30 dias", self.NOW)
        assert parsed.date().isoformat() == "2026-09-22"

    def test_um_dia(self):
        assert parse_created_at("1 dia", self.NOW).date().isoformat() == "2026-10-21"

    def test_hoje_e_horas(self):
        assert parse_created_at("hoje", self.NOW).date() == self.NOW.date()
        assert parse_created_at("3 horas", self.NOW).date() == self.NOW.date()

    def test_invalido(self):
        assert parse_created_at("sei lá", self.NOW) is None


class TestParseSearchPage:
    def test_extrai_vagas_do_payload(self, search_html):
        now = datetime(2026, 10, 22, 15, 0, tzinfo=_BRT)
        jobs = parse_search_page(search_html, now)
        assert len(jobs) >= 15
        job = jobs[0]
        assert job.source == "empregos"
        assert job.external_id.isdigit()
        assert job.title
        assert job.url == f"https://www.empregos.com.br/vaga/{job.external_id}"
        assert job.description
        # ids únicos
        ids = [j.external_id for j in jobs]
        assert len(ids) == len(set(ids))

    def test_datas_e_modalidade(self, search_html):
        now = datetime(2026, 10, 22, 15, 0, tzinfo=_BRT)
        jobs = parse_search_page(search_html, now)
        dated = [j for j in jobs if j.posted_at is not None]
        assert dated  # createdAtFormat "N dias" convertido
        remotes = [j for j in jobs if j.remote is True]
        assert remotes  # fixture tem vagas "Remoto"
        assert all(j.location == "Remoto" or j.location for j in remotes)

    def test_html_sem_payload(self):
        assert parse_search_page("<html><body>nada</body></html>") == []

    def test_payload_invalido(self):
        html = '<script id="__NUXT_DATA__" type="application/json">{broken</script>'
        assert parse_search_page(html) == []


class FakeSession:
    def __init__(self, html: str):
        self.html = html
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        return SimpleNamespace(text=self.html)


class TestEmpregosSource:
    def test_uma_pagina_por_query(self, search_html):
        session = FakeSession(search_html)
        source = EmpregosSource(session)
        list(source.search(SearchFilters(keywords="desenvolvedor",
                                         time_range="r1m", max_pages=5)))
        assert len(session.calls) == 1  # WAF: nunca pagina

    def test_filtra_por_periodo_sem_ordenar(self, search_html):
        source = EmpregosSource(FakeSession(search_html))
        all_jobs = list(source.search(SearchFilters(keywords="x", time_range="r1m",
                                                    max_pages=1)))
        source2 = EmpregosSource(FakeSession(search_html))
        recent = list(source2.search(SearchFilters(keywords="x", time_range="r3d",
                                                   max_pages=1)))
        assert len(recent) < len(all_jobs)
        cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        assert all(j.posted_at is None or j.posted_at >= cutoff for j in recent)

    def test_dedup_entre_queries(self, search_html):
        source = EmpregosSource(FakeSession(search_html))
        first = list(source.search(SearchFilters(keywords="a", time_range="r1m")))
        second = list(source.search(SearchFilters(keywords="b", time_range="r1m")))
        assert first and not second
