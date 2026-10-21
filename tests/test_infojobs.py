from datetime import datetime, timedelta, timezone
from pathlib import Path

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.infojobs import (
    InfoJobsSource, build_params, parse_job_detail, parse_posted_at,
    parse_search_page, time_range_to_timedelta,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_params_basics():
    params = build_params(SearchFilters(keywords="desenvolvedor fullstack"))
    assert params["palabra"] == "desenvolvedor fullstack"
    assert params["sortby"] == "PublishedDate"
    assert "Page" not in params
    assert build_params(SearchFilters(keywords="x"), page=3)["Page"] == "3"


def test_build_params_location():
    # "Brasil" não entra na palavra (site já é só BR)
    assert build_params(SearchFilters(keywords="dev", location="Brasil"))["palabra"] == "dev"
    assert build_params(
        SearchFilters(keywords="dev", location="Curitiba"))["palabra"] == "dev Curitiba"


def test_parse_posted_at():
    dt = parse_posted_at("2026/10/21 01:28:00")
    assert dt is not None and dt.utcoffset() == timedelta(hours=-3)
    assert parse_posted_at("lixo") is None


def test_time_range_to_timedelta():
    assert time_range_to_timedelta("r24h") == timedelta(days=1)
    assert time_range_to_timedelta("r2h") == timedelta(hours=2)
    assert time_range_to_timedelta("errado") == timedelta(days=7)


def test_parse_search_page():
    jobs = parse_search_page((FIXTURES / "infojobs_search.html").read_text())
    assert len(jobs) >= 10
    uids = [j.uid for j in jobs]
    assert len(uids) == len(set(uids))  # página repete cards; parser deduplica
    first = jobs[0]
    assert first.source == "infojobs"
    assert first.external_id.isdigit()
    assert first.title
    assert first.url.startswith("https://www.infojobs.com.br/vaga-de-")
    assert first.posted_at is not None
    assert any(" - " in j.location for j in jobs)  # "Cidade - UF"
    assert any(j.company for j in jobs)


def test_parse_search_page_empty():
    assert parse_search_page("<html></html>") == []


def test_parse_job_detail_print():
    desc = parse_job_detail((FIXTURES / "infojobs_print.html").read_text())
    assert len(desc) > 1000


class FakeResp:
    def __init__(self, text):
        self.text = text


def _card(job_id: str, posted: datetime) -> str:
    value = posted.astimezone(timezone(timedelta(hours=-3))).strftime("%Y/%m/%d %H:%M:%S")
    return f'''
    <div class="js_vacancyLoad" data-id="{job_id}" data-href="/vaga-de-dev__{job_id}.aspx">
      <div hidden class="js_date" data-value="{value}"></div>
      <h2 class="js_vacancyTitle">Dev {job_id}</h2>
      <div class="mb-8">Curitiba - PR</div>
    </div>'''


def test_search_filters_by_time_range_and_stops_on_stale_page():
    now = datetime.now(timezone.utc)
    fresh_page = "<html>" + _card("1", now) + _card("2", now - timedelta(hours=1)) + "</html>"
    stale_page = "<html>" + _card("3", now - timedelta(days=10)) + \
                 _card("4", now - timedelta(days=12)) + "</html>"
    pages = [fresh_page, stale_page, fresh_page]
    calls = []

    class FakeSession:
        def get(self, url, params=None):
            calls.append(params)
            return FakeResp(pages[len(calls) - 1])

    source = InfoJobsSource(FakeSession(), skip_detail=lambda uid: True)
    jobs = list(source.search(SearchFilters(keywords="x", time_range="r24h", max_pages=3)))
    assert [j.external_id for j in jobs] == ["1", "2"]  # só as dentro da janela
    assert len(calls) == 2  # página 2 toda velha -> para (não busca a 3ª)
