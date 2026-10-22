from pathlib import Path

from vaga_hunter.models import SearchFilters
from vaga_hunter.sources.programathor import (
    ProgramathorSource, build_params, parse_job_detail, parse_search_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_params():
    assert build_params(SearchFilters(keywords="react"))["search"] == "react"
    assert build_params(SearchFilters(keywords="x"), page=2)["page"] == "2"


def test_parse_search_page():
    jobs = parse_search_page((FIXTURES / "programathor_search.html").read_text())
    assert len(jobs) >= 10
    uids = [j.uid for j in jobs]
    assert len(uids) == len(set(uids))
    first = jobs[0]
    assert first.source == "programathor"
    assert first.external_id.isdigit()
    assert first.url.startswith("https://programathor.com.br/jobs/")
    assert any("Stack:" in j.description for j in jobs)
    assert any(j.company for j in jobs)
    assert any(j.location for j in jobs)
    # nível explícito entra no título quando ausente
    assert any("(" in j.title for j in jobs)
    # badge NOVA não gruda no título
    assert not any(j.title.rstrip(")").endswith("NOVA") or "NOVA (" in j.title
                   for j in jobs)
    # sem data no site
    assert all(j.posted_at is None for j in jobs)


def test_parse_search_page_empty():
    assert parse_search_page("<html></html>") == []


def test_parse_job_detail():
    description = parse_job_detail((FIXTURES / "programathor_detail.html").read_text())
    assert len(description) > 300
    assert "<" not in description[:200]


class FakeResp:
    def __init__(self, text):
        self.text = text


def test_search_stops_when_page_only_repeats():
    page = '''<html><div class="cell-list">
      <a href="/jobs/1-dev"><h3>Dev</h3></a></div></html>'''
    calls = []

    class FakeSession:
        def get(self, url, params=None):
            calls.append(params)
            return FakeResp(page)

    source = ProgramathorSource(FakeSession(), skip_detail=lambda uid: True)
    jobs = list(source.search(SearchFilters(keywords="x", max_pages=5)))
    assert len(jobs) == 1
    assert len(calls) == 2  # página 2 só repete -> para


def test_jsonld_robustness():
    from vaga_hunter.sources._jsonld import extract_jobposting, jobposting_description
    # description não-string não explode
    assert jobposting_description(
        '<script type="application/ld+json">{"@type":"JobPosting","description":123}</script>'
    ) == "123"
    # @graph e @type em lista
    graph = ('<script type="application/ld+json">{"@graph":[{"@type":["JobPosting"],'
             '"title":"X","description":"corpo da vaga"}]}</script>')
    assert extract_jobposting(graph).get("title") == "X"
    assert jobposting_description(graph) == "corpo da vaga"
