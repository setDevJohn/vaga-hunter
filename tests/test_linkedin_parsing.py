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
