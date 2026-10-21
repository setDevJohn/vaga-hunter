from datetime import datetime, timedelta, timezone
from pathlib import Path

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.catho import (
    CathoSource, build_url, parse_job_detail, parse_posted_tag,
    parse_search_page, query_slug,
)

FIXTURES = Path(__file__).parent / "fixtures"
BRT = timezone(timedelta(hours=-3))
NOW = datetime(2026, 10, 21, 15, 0, tzinfo=BRT)


def test_query_slug():
    assert query_slug("desenvolvedor fullstack júnior") == "desenvolvedor-fullstack-junior"
    assert query_slug("React Native") == "react-native"
    assert query_slug("desenvolvedor .net") == "desenvolvedor-.net"  # Catho usa pontos
    assert query_slug("c#") == "c"  # degradação documentada
    assert query_slug("###") == "vagas"  # nunca slug vazio


def test_build_url():
    url = build_url(SearchFilters(keywords="dev backend"), page=1)
    assert url == "https://www.catho.com.br/vagas/dev-backend/?order=dataAtualizacao"
    assert build_url(SearchFilters(keywords="dev"), page=3).endswith("&page=3")


def test_parse_posted_tag():
    assert parse_posted_tag("Publicada Hoje", NOW).day == 21
    assert parse_posted_tag("Publicada Ontem", NOW).day == 20
    dt = parse_posted_tag("Publicada em 16/10", NOW)
    assert (dt.day, dt.month, dt.year) == (16, 10, 2026)
    # mês futuro = ano passado
    dt2 = parse_posted_tag("Publicada em 05/12", NOW)
    assert dt2.year == 2025
    assert parse_posted_tag("sem data", NOW) is None
    # data de HOJE vista de manhã (antes do meio-dia) não pode virar ano passado
    morning = NOW.replace(hour=9)
    dt3 = parse_posted_tag("Publicada em 21/10", morning)
    assert dt3.year == 2026


def test_parse_search_page():
    jobs = parse_search_page((FIXTURES / "catho_search.html").read_text())
    assert len(jobs) >= 10
    uids = [j.uid for j in jobs]
    assert len(uids) == len(set(uids))
    first = jobs[0]
    assert first.source == "catho"
    assert first.external_id.isdigit()
    assert first.title
    assert first.url.startswith("https://www.catho.com.br/vagas/")
    assert sum(1 for j in jobs if j.posted_at) >= len(jobs) // 2
    assert any(j.company for j in jobs)
    assert any(j.location for j in jobs)


def test_parse_search_page_empty():
    assert parse_search_page("<html></html>") == []


def test_parse_job_detail_json_ld():
    desc = parse_job_detail((FIXTURES / "catho_detail.html").read_text())
    assert len(desc) > 200
    assert "usinagem" in desc.lower()


class FakeResp:
    def __init__(self, text):
        self.text = text


def _card(job_id: str, tag: str) -> str:
    return f'''
    <li data-offer-item="{job_id}">
      <span class="tag pub_x">{tag}</span>
      <h2 class="title_offer"><a href="/vagas/dev/{job_id}">Dev {job_id}</a></h2>
    </li>'''


def test_search_stops_on_stale_page():
    fresh = "<html>" + _card("1", "Publicada Hoje") + _card("2", "Publicada Ontem") + "</html>"
    stale = "<html>" + _card("3", "Publicada em 01/01") + "</html>"
    pages = [fresh, stale, fresh]
    calls = []

    class FakeSession:
        def get(self, url, params=None):
            calls.append(url)
            return FakeResp(pages[len(calls) - 1])

    source = CathoSource(FakeSession(), skip_detail=lambda uid: True)
    jobs = list(source.search(SearchFilters(keywords="dev", time_range="r3d", max_pages=3)))
    assert [j.external_id for j in jobs] == ["1", "2"]
    assert len(calls) == 2  # página 2 toda velha encerra


def test_search_stops_when_page_only_repeats_pinned():
    """Patrocinada pinada repetida em toda página não gera loop nem duplica."""
    page = "<html>" + _card("1", "Publicada Hoje") + "</html>"
    calls = []

    class FakeSession:
        def get(self, url, params=None):
            calls.append(url)
            return FakeResp(page)

    source = CathoSource(FakeSession(), skip_detail=lambda uid: True)
    jobs = list(source.search(SearchFilters(keywords="dev", time_range="r3d", max_pages=5)))
    assert [j.external_id for j in jobs] == ["1"]  # uma vez só
    assert len(calls) == 2  # página 2 só repete -> para (não busca as 5)
