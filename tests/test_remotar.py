"""Testes da fonte Remotar (API pública)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.remotar import (
    RemotarSource,
    build_params,
    parse_search_json,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_json() -> str:
    return (FIXTURES / "remotar_search.json").read_text()


class TestBuildParams:
    def test_basico(self):
        filters = SearchFilters(keywords="desenvolvedor")
        assert build_params(filters) == {"search": "desenvolvedor"}

    def test_paginacao(self):
        filters = SearchFilters(keywords="dev")
        assert build_params(filters, page=3) == {"search": "dev", "page": "3"}


class TestParseSearchJson:
    def test_campos(self, search_json):
        jobs, last_page = parse_search_json(search_json)
        assert last_page >= 1
        assert len(jobs) == 50
        job = jobs[0]
        assert job.source == "remotar"
        assert job.title
        assert job.company
        assert job.remote is True
        assert job.location == "Remoto"
        assert job.url == f"https://remotar.com.br/job/{job.external_id}"
        assert job.posted_at is not None and job.posted_at.tzinfo is not None
        assert job.description

    def test_subtitle_vai_para_descricao(self, search_json):
        raw = json.loads(search_json)
        item = next(j for j in raw["data"] if j.get("subtitle"))
        jobs, _ = parse_search_json(search_json)
        job = next(j for j in jobs if j.external_id == str(item["id"]))
        assert item["subtitle"].strip() in job.description

    def test_payload_vazio(self):
        assert parse_search_json('{"meta":{},"data":[]}') == ([], 1)


def _payload(jobs: list[dict], last_page: int = 1) -> str:
    return json.dumps({"meta": {"last_page": last_page}, "data": jobs})


def _job(i: int, created: str) -> dict:
    return {"id": i, "title": f"Dev {i}", "subtitle": "",
            "description": "<p>desc</p>", "createdAt": created,
            "company": {"name": "Empresa"}, "companyDisplayName": None}


class FakeSession:
    def __init__(self, pages: dict[int, str]):
        self.pages = pages
        self.calls: list[dict] = []

    def get(self, url, params=None):
        self.calls.append(params or {})
        page = int((params or {}).get("page", 1))
        return SimpleNamespace(text=self.pages.get(page, _payload([])))


class TestRemotarSource:
    def test_para_quando_pagina_fora_da_janela(self):
        now = datetime.now(timezone.utc)
        fresh = (now - timedelta(hours=2)).isoformat()
        old = (now - timedelta(days=9)).isoformat()
        pages = {1: _payload([_job(1, fresh)], last_page=3),
                 2: _payload([_job(2, old)], last_page=3),
                 3: _payload([_job(3, fresh)], last_page=3)}
        source = RemotarSource(FakeSession(pages))
        jobs = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                max_pages=5)))
        # página 2 toda fora da janela encerra; página 3 nunca é buscada
        assert [j.external_id for j in jobs] == ["1"]

    def test_respeita_last_page(self):
        now = datetime.now(timezone.utc).isoformat()
        pages = {1: _payload([_job(1, now)], last_page=1)}
        session = FakeSession(pages)
        source = RemotarSource(session)
        list(source.search(SearchFilters(keywords="dev", time_range="r1m",
                                         max_pages=5)))
        assert len(session.calls) == 1

    def test_dedup_entre_queries(self):
        now = datetime.now(timezone.utc).isoformat()
        pages = {1: _payload([_job(1, now)], last_page=1)}
        source = RemotarSource(FakeSession(pages))
        first = list(source.search(SearchFilters(keywords="dev", time_range="r1m")))
        second = list(source.search(SearchFilters(keywords="react", time_range="r1m")))
        assert len(first) == 1 and len(second) == 0
