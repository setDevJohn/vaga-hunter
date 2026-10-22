"""Testes da fonte GeekHunter."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaga_hunter.models import Job, SearchFilters
from vaga_hunter.sources.geekhunter import (
    BLOCK_STATUS,
    GeekhunterSource,
    build_params,
    parse_job_detail,
    parse_relative_date,
    parse_search_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_html() -> str:
    return (FIXTURES / "geekhunter_search.html").read_text()


@pytest.fixture
def detail_html() -> str:
    return (FIXTURES / "geekhunter_detail.html").read_text()


class TestBuildParams:
    def test_termo_sempre_ordenado_por_data(self):
        params = build_params(SearchFilters(keywords=" react "))
        assert params == {"searchTerm": "react", "orderBy": "moreRecent"}

    def test_paginacao(self):
        assert build_params(SearchFilters(keywords="dev"), page=3)["page"] == "3"


class TestParseRelativeDate:
    NOW = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)

    def test_horas(self):
        parsed = parse_relative_date("Publicada há 3 horas", self.NOW)
        assert parsed == self.NOW - timedelta(hours=3)

    def test_singular(self):
        assert parse_relative_date("Publicada há 1 dia", self.NOW) == \
            self.NOW - timedelta(days=1)

    def test_meses(self):
        assert parse_relative_date("Publicada há 2 meses", self.NOW) == \
            self.NOW - timedelta(days=60)

    def test_invalido(self):
        assert parse_relative_date("sem data nenhuma") is None


class TestParseSearchPage:
    def test_extrai_cards(self, search_html):
        jobs = parse_search_page(search_html)
        assert len(jobs) == 25  # cards duplicados (desktop+mobile) deduplicados
        job = jobs[0]
        assert job.source == "geekhunter"
        assert "/" in job.external_id  # <empresa>/<slug>
        assert job.title
        assert job.url.startswith("https://www.geekhunter.com/pt/")

    def test_datas_relativas_e_ordenacao(self, search_html):
        jobs = parse_search_page(search_html)
        dates = [j.posted_at for j in jobs if j.posted_at]
        assert len(dates) >= 20  # quase todos os cards têm "Publicada há"
        assert dates == sorted(dates, reverse=True)

    def test_html_vazio(self):
        assert parse_search_page("<html></html>") == []


class TestParseJobDetail:
    def _card(self) -> Job:
        return Job(source="geekhunter",
                   external_id="techmagic/middle-salesforce-developer-1",
                   title="Desenvolvedor Salesforce Pleno",
                   url="https://www.geekhunter.com/pt/techmagic/jobs/"
                       "middle-salesforce-developer-1")

    def test_enriquece_com_json_ld(self, detail_html):
        job = parse_job_detail(detail_html, self._card())
        assert job.company == "TechMagic"
        assert "Salesforce" in job.description
        assert "Skills:" in job.description
        assert job.remote is True  # jobLocationType TELECOMMUTE
        assert job.location  # addressLocality do jobLocation

    def test_data_do_card_tem_prioridade(self, detail_html):
        card = self._card()
        card.posted_at = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
        job = parse_job_detail(detail_html, card)
        assert job.posted_at == card.posted_at

    def test_data_do_json_ld_como_fallback(self, detail_html):
        job = parse_job_detail(detail_html, self._card())
        assert job.posted_at == datetime(2026, 10, 22, tzinfo=timezone.utc)

    def test_sem_json_ld_devolve_card(self):
        card = self._card()
        assert parse_job_detail("<html></html>", card) is card


class FakeSession:
    def __init__(self, pages: dict[int, str], detail: str = "<html></html>"):
        self.pages = pages
        self.detail = detail
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        if "/vagas" in url:
            page = int((params or {}).get("page", 1))
            return SimpleNamespace(text=self.pages.get(page, "<html></html>"))
        return SimpleNamespace(text=self.detail)


def _card_html(slug: str, published: str) -> str:
    return (f'<div><p>Publicada há {published}</p>'
            f'<h3><a href="https://www.geekhunter.com/pt/empresa/jobs/{slug}">'
            f'Dev {slug}</a></h3></div>')


def _page(cards: list[str]) -> str:
    return "<html><body>" + "".join(cards) + "</body></html>"


class TestGeekhunterSource:
    def test_block_status_inclui_403(self):
        assert 403 in BLOCK_STATUS

    def test_corta_por_periodo_e_para(self):
        pages = {1: _page([_card_html("a-1", "2 horas"),
                           _card_html("b-1", "10 dias")]),
                 2: _page([_card_html("c-1", "11 dias")])}
        source = GeekhunterSource(FakeSession(pages),
                                  skip_detail=lambda uid: True)
        jobs = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                max_pages=3)))
        assert [j.external_id for j in jobs] == ["empresa/a-1"]
        # página 2 é toda stale -> early-break (sem buscar página 3)
        search_calls = [c for c in source.session.calls if "/vagas" in c]
        assert len(search_calls) == 2

    def test_vaga_fora_da_janela_nao_queima_dedup(self):
        pages = {1: _page([_card_html("a-1", "2 dias")])}
        source = GeekhunterSource(FakeSession(pages),
                                  skip_detail=lambda uid: True)
        short = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                 max_pages=1)))
        wide = list(source.search(SearchFilters(keywords="dev", time_range="r7d",
                                                max_pages=1)))
        assert short == [] and [j.external_id for j in wide] == ["empresa/a-1"]

    def test_dedup_entre_queries(self):
        pages = {1: _page([_card_html("a-1", "2 horas")])}
        source = GeekhunterSource(FakeSession(pages),
                                  skip_detail=lambda uid: True)
        first = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                 max_pages=1)))
        second = list(source.search(SearchFilters(keywords="react", time_range="r24h",
                                                  max_pages=1)))
        assert len(first) == 1 and len(second) == 0

    def test_busca_detalhe_so_de_vaga_nova(self, detail_html):
        pages = {1: _page([_card_html("a-1", "2 horas")])}
        session = FakeSession(pages, detail=detail_html)
        source = GeekhunterSource(session, skip_detail=lambda uid: False)
        jobs = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                max_pages=1)))
        assert len(jobs) == 1
        assert jobs[0].company == "TechMagic"  # veio do detalhe
        detail_calls = [c for c in session.calls if "/jobs/" in c]
        assert len(detail_calls) == 1

    def test_sessao_do_pipeline_breaker_imediato(self, tmp_path):
        from vaga_hunter.config import Config
        from vaga_hunter.pipeline import build_sources
        from vaga_hunter.storage import Storage

        config = Config()
        for name in ("linkedin_jobs", "linkedin_posts", "infojobs", "catho",
                     "programathor", "trampos", "gupy", "remotar"):
            getattr(config.sources, name).enabled = False
        config.sources.geekhunter.enabled = True
        storage = Storage(tmp_path / "t.db")

        (source,) = build_sources(config, storage)
        assert source.session.breaker_threshold == 1
        assert source.session.block_status == BLOCK_STATUS
