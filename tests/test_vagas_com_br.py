from datetime import datetime, timedelta, timezone
from pathlib import Path

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.vagas_com_br import (
    VagasComBrSource, build_url, parse_job_detail, parse_posted_text,
    parse_search_page, query_slug,
)

FIXTURES = Path(__file__).parent / "fixtures"
BRT = timezone(timedelta(hours=-3))
NOW = datetime(2026, 10, 21, 15, 0, tzinfo=BRT)


def test_build_url_and_slug():
    assert query_slug("programador júnior") == "programador-junior"
    url = build_url(SearchFilters(keywords="programador"))
    assert url == "https://www.vagas.com.br/vagas-de-programador?ordenar_por=mais_recentes"
    assert build_url(SearchFilters(keywords="ti"), page=2).endswith("&pagina=2")


def test_parse_posted_text():
    assert parse_posted_text("Hoje", NOW).day == 21
    assert parse_posted_text("Ontem", NOW).day == 20
    assert parse_posted_text("Há 7 dias", NOW).day == 14
    dt = parse_posted_text("01/10/2026", NOW)
    assert (dt.day, dt.month, dt.year) == (1, 10, 2026)
    assert parse_posted_text("sei lá", NOW) is None


def test_parse_search_page():
    jobs = parse_search_page((FIXTURES / "vagascombr_search.html").read_text())
    assert len(jobs) >= 30
    uids = [j.uid for j in jobs]
    assert len(uids) == len(set(uids))
    first = jobs[0]
    assert first.source == "vagas_com_br"
    assert first.external_id.isdigit()
    assert first.url.startswith("https://www.vagas.com.br/vagas/")
    assert any(j.posted_at for j in jobs)
    assert any(j.company for j in jobs)
    # nível explícito do site entra no título quando o título não o traz
    assert any("(" in j.title and ")" in j.title for j in jobs)


def test_parse_search_page_empty():
    assert parse_search_page("<html></html>") == []


def test_parse_job_detail_json_ld():
    description, posted_at = parse_job_detail(
        (FIXTURES / "vagascombr_detail.html").read_text())
    assert len(description) > 500
    assert "<" not in description[:200]
    assert posted_at is not None and posted_at.year == 2026


class FakeResp:
    def __init__(self, text):
        self.text = text


def _card(job_id: str, date_text: str) -> str:
    return f'''
    <li class="vaga">
      <a class="link-detalhes-vaga" data-id-vaga="{job_id}" href="/vagas/v{job_id}/dev">Dev {job_id}</a>
      <span class="data-publicacao"><i></i>{date_text}</span>
    </li>'''


def test_search_dedups_page_overlap_and_stops_on_stale():
    page1 = "<html>" + _card("1", "Hoje") + _card("2", "Ontem") + "</html>"
    page2 = "<html>" + _card("2", "Ontem") + _card("3", "Há 30 dias") + "</html>"
    page3 = "<html>" + _card("3", "Há 30 dias") + "</html>"
    pages = [page1, page2, page3]
    calls = []

    class FakeSession:
        def get(self, url, params=None):
            calls.append(url)
            return FakeResp(pages[len(calls) - 1])

    source = VagasComBrSource(FakeSession(), skip_detail=lambda uid: True)
    jobs = list(source.search(SearchFilters(keywords="x", time_range="r3d", max_pages=3)))
    # job 2 repetido na p2 não duplica; job 3 (30 dias) filtrado pela janela
    assert [j.external_id for j in jobs] == ["1", "2"]
    # p2 só tinha 1 não-visto e ele era velho -> stale == unseen -> para
    assert len(calls) == 2


def test_level_injection_accent_insensitive():
    html = '''<html><li class="vaga">
      <a class="link-detalhes-vaga" data-id-vaga="9" href="/vagas/v9/dev">Dev SENIOR</a>
      <span class="nivelVaga">Sênior</span>
    </li></html>'''
    jobs = parse_search_page(html)
    assert jobs[0].title == "Dev SENIOR"  # não duplica "(Sênior)"
