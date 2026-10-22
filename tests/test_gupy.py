"""Testes da fonte Gupy (rota _next/data)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.gupy import (
    GupySource,
    extract_build_id,
    parse_search_json,
    search_page_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_json() -> str:
    return (FIXTURES / "gupy_search.json").read_text()


class TestBuildId:
    def test_extrai_do_html(self):
        html = '<script>{"props":{},"buildId":"cV0_DMl2AbEm4R-kj_nZu","x":1}</script>'
        assert extract_build_id(html) == "cV0_DMl2AbEm4R-kj_nZu"

    def test_sem_build_id_levanta(self):
        with pytest.raises(ValueError):
            extract_build_id("<html></html>")


class TestUrl:
    def test_pagina_1(self):
        url = search_page_url("abc123", "desenvolvedor react")
        assert url == ("https://portal.gupy.io/_next/data/abc123/job-search/"
                       "term=desenvolvedor%20react.json")

    def test_paginacao(self):
        assert search_page_url("abc", "dev", page=2).endswith(
            "term=dev&page=2.json")


class TestParseSearchJson:
    def test_campos(self, search_json):
        jobs, total = parse_search_json(search_json)
        assert total > 0
        assert len(jobs) == 12
        job = jobs[0]
        assert job.source == "gupy"
        assert job.external_id
        assert job.title
        assert job.company
        assert job.url.startswith("http")
        assert job.posted_at is not None and job.posted_at.tzinfo is not None
        assert job.description  # descrição completa já na listagem

    def test_ordenado_por_data_desc(self, search_json):
        jobs, _ = parse_search_json(search_json)
        dates = [j.posted_at for j in jobs if j.posted_at]
        assert dates == sorted(dates, reverse=True)

    def test_workplace_remote(self, search_json):
        jobs, _ = parse_search_json(search_json)
        remotes = {j.external_id: j.remote for j in jobs}
        raw = json.loads(search_json)["pageProps"]["initialJobList"]["data"]
        for item in raw:
            expected = {"remote": True, "hybrid": False,
                        "on-site": False}.get(str(item["workplaceType"]).lower())
            assert remotes[str(item["id"])] == expected

    def test_payload_vazio(self):
        assert parse_search_json('{"pageProps":{}}') == ([], 0)


class FakeSession:
    def __init__(self, pages: dict[int, str], html: str = ""):
        self.pages = pages
        self.html = html
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        if "_next/data" not in url:
            return SimpleNamespace(text=self.html)
        page = 1
        if "&page=" in url:
            page = int(url.split("&page=")[1].split(".json")[0])
        return SimpleNamespace(text=self.pages.get(page, '{"pageProps":{}}'))


def _payload(jobs: list[dict], total: int) -> str:
    return json.dumps({"pageProps": {"initialJobList": {
        "data": jobs, "pagination": {"total": total}}}})


def _job(i: int, published: str) -> dict:
    return {"id": i, "name": f"Dev {i}", "careerPageName": "Empresa",
            "description": "desc", "publishedDate": published,
            "workplaceType": "remote", "city": "", "state": "",
            "jobUrl": f"https://x.gupy.io/job/{i}"}


class TestGupySource:
    def test_corta_por_periodo_e_para(self):
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(hours=1)).isoformat()
        old = (now - timedelta(days=10)).isoformat()
        html = '{"buildId":"b1"}'
        pages = {1: _payload([_job(1, fresh), _job(2, old)], 30),
                 2: _payload([_job(3, old)], 30)}
        source = GupySource(FakeSession(pages, html))
        jobs = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                max_pages=3)))
        assert [j.external_id for j in jobs] == ["1"]
        # página 2 não deve ser buscada: página 1 já não estava toda na janela?
        # (1 fresh + 1 stale -> continua; página 2 toda stale -> para)

    def test_renova_build_id_quando_rota_falha(self):
        now = datetime.now(timezone.utc).isoformat()

        class Session:
            def __init__(self):
                self.calls = []
                self.build = "velho"

            def get(self, url, params=None):
                self.calls.append(url)
                if "_next/data/velho" in url:
                    return SimpleNamespace(text="<html>404</html>")
                if "_next/data/novo" in url:
                    return SimpleNamespace(text=_payload([_job(1, now)], 1))
                # página HTML: na 2ª extração devolve buildId novo
                self.build = "novo" if self.build == "velho" else self.build
                return SimpleNamespace(text=f'{{"buildId":"{self.build}"}}')

        source = GupySource(Session())
        source._build_id = "velho"
        jobs = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                max_pages=1)))
        assert [j.external_id for j in jobs] == ["1"]

    def test_dedup_entre_queries(self):
        now = datetime.now(timezone.utc).isoformat()
        pages = {1: _payload([_job(1, now)], 1)}
        source = GupySource(FakeSession(pages, '{"buildId":"b"}'))
        first = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                 max_pages=1)))
        second = list(source.search(SearchFilters(keywords="react", time_range="r24h",
                                                  max_pages=1)))
        assert len(first) == 1 and len(second) == 0
