from pathlib import Path

from vaga_hunter.sources.linkedin_jobs import parse_job_detail, parse_search_page

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_search_page_extracts_jobs():
    html = (FIXTURES / "search_page.html").read_text()
    jobs = parse_search_page(html)
    assert len(jobs) > 0
    first = jobs[0]
    assert first.external_id.isdigit()
    assert first.title
    assert first.company
    assert first.url.startswith("https://www.linkedin.com/jobs/view/")
    assert first.source == "linkedin_jobs"
    assert first.uid == f"linkedin_jobs:{first.external_id}"
    # pelo menos algumas vagas têm data
    assert any(job.posted_at is not None for job in jobs)


def test_parse_search_page_empty_html():
    assert parse_search_page("<html><body></body></html>") == []


def test_parse_job_detail_extracts_description():
    html = (FIXTURES / "job_detail.html").read_text()
    description = parse_job_detail(html)
    assert len(description) > 100


def test_parse_job_detail_empty():
    assert parse_job_detail("<html></html>") == ""


def test_search_paginates_by_actual_page_size(monkeypatch):
    """start avança pelo nº real de vagas da página (LinkedIn varia o tamanho)."""
    from pathlib import Path
    from vaga_hunter.models import SearchFilters
    from vaga_hunter.sources.linkedin_jobs import LinkedInJobsSource

    page_html = (Path(__file__).parent / "fixtures" / "search_page.html").read_text()
    starts = []

    class FakeResp:
        def __init__(self, text): self.text = text

    class FakeSession:
        def get(self, url, params=None):
            starts.append(params["start"])
            # 2 páginas com conteúdo, terceira vazia
            return FakeResp(page_html if len(starts) <= 2 else "<html></html>")

    source = LinkedInJobsSource(FakeSession(), skip_detail=lambda uid: True)
    jobs = list(source.search(SearchFilters(keywords="x", max_pages=5)))
    per_page = len(jobs) // 2
    assert per_page > 0
    assert starts == ["0", str(per_page), str(per_page * 2)]


def test_posted_at_timezone_aware_meio_dia_brt():
    html = (FIXTURES / "search_page.html").read_text()
    jobs = parse_search_page(html)
    dated = [j for j in jobs if j.posted_at]
    assert dated
    for job in dated:
        # card guest traz só a data: convenção do projeto = meio-dia BRT
        assert job.posted_at.tzinfo is not None
        assert job.posted_at.hour == 12
