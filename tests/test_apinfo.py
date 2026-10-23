"""Testes da fonte APinfo."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.apinfo import (
    ApinfoSource,
    build_form,
    parse_date,
    parse_search_page,
)

FIXTURES = Path(__file__).parent / "fixtures"
_BRT = timezone(timedelta(hours=-3))


@pytest.fixture
def search_html() -> str:
    return (FIXTURES / "apinfo_search.html").read_bytes().decode("iso-8859-1")


class TestBuildForm:
    NOW = datetime(2026, 10, 23, 15, 0, tzinfo=_BRT)

    def test_periodo_server_side(self):
        form = build_form(SearchFilters(keywords="react", time_range="r3d"),
                          now=self.NOW)
        assert form["ddmmaa1"] == "20/10/26"
        assert form["keyw"] == "react"
        assert form["onde"] == "1" and form["andor"] == "1"
        assert "pkey" not in form

    def test_paginacao_leva_pkey_e_tcv(self):
        form = build_form(SearchFilters(keywords="dev"), page=2,
                          pkey="abcDEF", tcv="188", now=self.NOW)
        assert form["pag"] == "2"
        assert form["pkey"] == "abcDEF"
        assert form["tcv"] == "188"


class TestParseDate:
    def test_ddmmaa_vira_meio_dia_brt(self):
        assert parse_date("23/10/26") == datetime(2026, 10, 23, 12, tzinfo=_BRT)

    def test_invalida(self):
        assert parse_date("99/99/99") is None


class TestParseSearchPage:
    def test_extrai_cards(self, search_html):
        jobs, total_pages, pkey, tcv = parse_search_page(search_html)
        assert total_pages == 24
        assert pkey  # form de paginação traz o pkey da sessão
        assert tcv == "188"
        assert len(jobs) >= 8
        job = jobs[0]
        assert job.source == "apinfo"
        assert job.external_id.isdigit()
        assert job.title
        assert job.description and "Código" in job.description
        assert job.posted_at is not None and job.posted_at.tzinfo is not None

    def test_empresa_e_codigo(self, search_html):
        jobs, *_ = parse_search_page(search_html)
        capitani = [j for j in jobs if j.external_id == "85278"]
        assert capitani, "vaga 85278 (CAPITANI) está na fixture"
        job = capitani[0]
        assert job.company == "CAPITANI GROUP"
        assert "Dot Net" in job.title
        assert job.location == "Barueri - SP"
        assert job.remote is None

    def test_home_office_vira_remoto(self, search_html):
        jobs, *_ = parse_search_page(search_html)
        remotes = [j for j in jobs if j.remote]
        assert remotes, "fixture tem vaga Home Office"
        assert all(j.location == "Remoto" for j in remotes)

    def test_html_vazio(self):
        assert parse_search_page("<html></html>") == ([], 1, "", "1")


def _card(code: str, date: str, title: str = "Dev") -> str:
    return (f'<div class="box-vagas"><div class="info-data">São Paulo - SP - '
            f'{date}</div><div class="cargo">{title}</div>'
            f'<div class="texto">desc\nEmpresa .....: Acme\n'
            f'Código .......: {code}</div></div>')


def _page(cards: list[str], total: int = 1, pkey: str = "k1") -> str:
    return (f'<html><body><input name="pkey" value="{pkey}">'
            f'<input name="tcv" value="9">'
            f'<strong>Página 1 de {total}</strong>{"".join(cards)}</body></html>')


class FakeSession:
    def __init__(self, pages: dict[int, str]):
        self.pages = pages
        self.forms: list[dict] = []

    def post(self, url, data=None):
        self.forms.append(data or {})
        page = int((data or {}).get("pag", 1))
        return SimpleNamespace(
            content=self.pages.get(page, "<html></html>").encode("iso-8859-1"))


def _today() -> str:
    return datetime.now(_BRT).strftime("%d/%m/%y")


def _days_ago(days: int) -> str:
    return (datetime.now(_BRT) - timedelta(days=days)).strftime("%d/%m/%y")


class TestApinfoSource:
    def test_corta_por_periodo_e_para(self):
        pages = {1: _page([_card("1", _today()), _card("2", _days_ago(10))],
                          total=3),
                 2: _page([_card("3", _days_ago(11))], total=3)}
        source = ApinfoSource(FakeSession(pages))
        jobs = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                max_pages=3)))
        assert [j.external_id for j in jobs] == ["1"]
        assert len(source.session.forms) == 2  # página 3 não é buscada

    def test_paginacao_reusa_pkey_da_resposta(self):
        pages = {1: _page([_card("1", _today())], total=2, pkey="sessao123"),
                 2: _page([_card("2", _today())], total=2, pkey="sessao123")}
        source = ApinfoSource(FakeSession(pages))
        jobs = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                max_pages=2)))
        assert len(jobs) == 2
        assert "pkey" not in source.session.forms[0]
        assert source.session.forms[1]["pkey"] == "sessao123"
        assert source.session.forms[1]["tcv"] == "9"

    def test_respeita_total_de_paginas(self):
        pages = {1: _page([_card("1", _today())], total=1)}
        source = ApinfoSource(FakeSession(pages))
        list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                         max_pages=5)))
        assert len(source.session.forms) == 1

    def test_vaga_fora_da_janela_nao_queima_dedup(self):
        pages = {1: _page([_card("1", _days_ago(2))])}
        source = ApinfoSource(FakeSession(pages))
        short = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                 max_pages=1)))
        wide = list(source.search(SearchFilters(keywords="dev", time_range="r7d",
                                                max_pages=1)))
        assert short == [] and [j.external_id for j in wide] == ["1"]

    def test_dedup_entre_queries(self):
        pages = {1: _page([_card("1", _today())])}
        source = ApinfoSource(FakeSession(pages))
        first = list(source.search(SearchFilters(keywords="dev", time_range="r24h",
                                                 max_pages=1)))
        second = list(source.search(SearchFilters(keywords="react", time_range="r24h",
                                                  max_pages=1)))
        assert len(first) == 1 and len(second) == 0
