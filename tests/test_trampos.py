"""Testes da fonte Trampos.co (sitemap + JSON-LD)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.trampos import (
    SitemapEntry,
    TramposSource,
    parse_job_detail,
    parse_sitemap,
    slug_matches,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sitemap_xml() -> str:
    return (FIXTURES / "trampos_sitemap.xml").read_text()


@pytest.fixture
def detail_html() -> str:
    return (FIXTURES / "trampos_detail.html").read_text()


class TestParseSitemap:
    def test_extrai_vagas_com_lastmod(self, sitemap_xml):
        entries = parse_sitemap(sitemap_xml)
        ids = {e.external_id for e in entries}
        assert ids == {"774791", "774900", "774901", "700000"}
        front = next(e for e in entries if e.external_id == "774791")
        assert front.slug == "desenvolvedor-a-front-end"
        assert front.url.endswith("/oportunidade/774791-desenvolvedor-a-front-end")
        assert front.lastmod == datetime.fromisoformat("2026-10-09T09:49:09-03:00")

    def test_ignora_urls_que_nao_sao_vaga(self, sitemap_xml):
        # /oportunidades (listagem) não vira entry
        assert all("oportunidade/" in e.url for e in parse_sitemap(sitemap_xml))

    def test_sitemap_vazio(self):
        assert parse_sitemap("<urlset></urlset>") == []


class TestSlugMatches:
    def test_palavra_simples(self):
        assert slug_matches("desenvolvedor", "desenvolvedor-a-front-end")
        assert not slug_matches("desenvolvedor", "diretor-a-de-arte")

    def test_fullstack_casa_full_stack(self):
        assert slug_matches("full stack", "desenvolvedor-a-full-stack")
        assert slug_matches("fullstack", "desenvolvedor-a-full-stack")

    def test_frontend_casa_front_end(self):
        assert slug_matches("front end", "desenvolvedor-a-front-end")
        assert slug_matches("frontend", "desenvolvedor-a-front-end")

    def test_stopwords_ignoradas(self):
        assert slug_matches("analista de sistemas", "analista-de-sistemas-back-end")

    def test_acentos_na_query(self):
        assert slug_matches("comunicação", "atendimento-comunicacao-interna")

    def test_todas_palavras_precisam_casar(self):
        assert not slug_matches("desenvolvedor mobile", "desenvolvedor-a-front-end")

    def test_query_vazia_nao_casa(self):
        assert not slug_matches("", "desenvolvedor-a-front-end")
        assert not slug_matches("de", "diretor-a-de-arte")


class TestParseJobDetail:
    @pytest.fixture
    def entry(self) -> SitemapEntry:
        return SitemapEntry(
            external_id="774791",
            slug="desenvolvedor-a-front-end",
            url="https://www.trampos.co/oportunidade/774791-desenvolvedor-a-front-end",
            lastmod=datetime.fromisoformat("2026-10-09T09:49:09-03:00"),
        )

    def test_campos_do_jsonld(self, detail_html, entry):
        job = parse_job_detail(detail_html, entry)
        assert job.source == "trampos"
        assert job.external_id == "774791"
        assert job.title == "Desenvolvedor(a) Front-end"
        assert job.company == "Heal Marketing"
        assert "Desenvolvedor" in job.description
        assert "Contrato: TEMPORARY" in job.description
        assert job.posted_at is not None
        assert job.posted_at.date().isoformat() == "2026-10-09"

    def test_telecommute_marca_remoto(self, detail_html, entry):
        job = parse_job_detail(detail_html, entry)
        assert job.remote is True
        assert job.location == "Remoto"

    def test_html_sem_jsonld_usa_fallbacks(self, entry):
        html = ('<html><head><meta property="og:description" '
                'content="Vaga de dev"/></head><body></body></html>')
        job = parse_job_detail(html, entry)
        assert job.title == "Desenvolvedor a front end"  # derivado do slug
        assert job.description == "Vaga de dev"
        assert job.posted_at == entry.lastmod  # fallback no lastmod
        assert job.remote is None


class FakeSession:
    def __init__(self, sitemap_xml: str, detail_html: str):
        self.sitemap_xml = sitemap_xml
        self.detail_html = detail_html
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        text = self.sitemap_xml if "sitemap" in url else self.detail_html
        return SimpleNamespace(text=text, content=text.encode())


class TestTramposSource:
    def _filters(self, keywords: str, time_range: str = "r7d") -> SearchFilters:
        return SearchFilters(keywords=keywords, time_range=time_range)

    def _source(self, sitemap_xml, detail_html, skip=None):
        session = FakeSession(sitemap_xml, detail_html)
        return TramposSource(session, skip_detail=skip), session

    def test_filtra_por_query_e_periodo(self, sitemap_xml, detail_html,
                                        monkeypatch):
        # congela "agora" perto do lastmod das fixtures
        now = datetime.fromisoformat("2026-10-22T12:00:00+00:00")

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz else now.replace(tzinfo=None)

        monkeypatch.setattr("vaga_hunter.sources.trampos.datetime", FakeDatetime)
        source, session = self._source(sitemap_xml, detail_html)
        jobs = list(source.search(self._filters("desenvolvedor", "r3d")))
        # 774900 (full stack, ontem) casa; 774791 (09/10) fora da janela;
        # 774901 (diretor de arte) não casa; 700000 (agosto) fora da janela
        assert [j.external_id for j in jobs] == ["774900"]

    def test_sitemap_baixado_uma_vez_para_varias_queries(self, sitemap_xml,
                                                         detail_html):
        source, session = self._source(sitemap_xml, detail_html)
        list(source.search(self._filters("desenvolvedor", "r1m")))
        list(source.search(self._filters("react", "r1m")))
        sitemap_calls = [u for u in session.calls if "sitemap" in u]
        assert len(sitemap_calls) == 1

    def test_mesma_vaga_nao_repete_entre_queries(self, sitemap_xml, detail_html):
        source, _ = self._source(sitemap_xml, detail_html)
        first = list(source.search(self._filters("desenvolvedor", "r1m")))
        second = list(source.search(self._filters("full stack", "r1m")))
        ids = [j.external_id for j in first + second]
        assert len(ids) == len(set(ids))

    def test_skip_detail_evita_fetch(self, sitemap_xml, detail_html):
        source, session = self._source(sitemap_xml, detail_html,
                                       skip=lambda uid: True)
        jobs = list(source.search(self._filters("desenvolvedor", "r1m")))
        assert jobs  # ainda retorna os jobs (para touch do last_seen)
        detail_calls = [u for u in session.calls if "oportunidade/" in u]
        assert detail_calls == []
        assert all(j.title for j in jobs)


class TestSlugMatchesBordas:
    def test_nao_casa_substring_de_token(self):
        assert not slug_matches("react", "preact-developer-jr")

    def test_query_separada_casa_slug_compacto(self):
        assert slug_matches("full stack", "fullstack-developer")
        assert slug_matches("front end", "frontend-pleno")
