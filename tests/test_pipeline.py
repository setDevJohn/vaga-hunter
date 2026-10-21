from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from vaga_hunter.config import Config
from vaga_hunter.http_client import SourceBlockedError
from vaga_hunter.models import Job, SearchFilters
from vaga_hunter.pipeline import run_search
from vaga_hunter.scoring import Scorer
from vaga_hunter.sources.base import JobSource
from vaga_hunter.storage import Storage


class FakeSource(JobSource):
    name = "fake"

    def __init__(self, jobs: list[Job], blocked: bool = False):
        self.jobs = jobs
        self.blocked = blocked

    def search(self, filters: SearchFilters) -> Iterator[Job]:
        if self.blocked:
            raise SourceBlockedError("3 respostas 429 consecutivas")
        yield from self.jobs


class FakeNotifier:
    def __init__(self):
        self.sent: list[list[Job]] = []

    def notify(self, jobs: list[Job]) -> None:
        self.sent.append(jobs)


@pytest.fixture
def env(tmp_path):
    config = Config()
    config.search.queries = ["react"]
    config.profile.roles = ["fullstack", "backend"]
    config.profile.include_keywords = ["react", "node"]
    config.profile.exclude_keywords_hard = ["estágio"]
    config.scoring.threshold = 50
    storage = Storage(tmp_path / "p.db")
    scorer = Scorer(config.profile, config.scoring)
    yield config, storage, scorer
    storage.close()


def _job(external_id: str, title: str) -> Job:
    return Job(source="fake", external_id=external_id, title=title,
               description="react node remoto",
               posted_at=datetime.now(timezone.utc) - timedelta(hours=6))


def test_run_persists_scores_and_notifies(env):
    config, storage, scorer = env
    jobs = [_job("1", "Desenvolvedor Fullstack Pleno"),
            _job("2", "Estágio em vendas")]
    notifier = FakeNotifier()
    report = run_search(config, storage, [FakeSource(jobs)], scorer, [notifier])

    assert report.found == 2 and report.new == 2
    assert report.notified == 1  # só a vaga acima do threshold
    assert len(notifier.sent[0]) == 1
    assert notifier.sent[0][0].external_id == "1"
    rows = storage.list_jobs()
    assert {r["status"] for r in rows} == {"notified", "new"}


def test_second_run_dedups_and_does_not_renotify(env):
    config, storage, scorer = env
    jobs = [_job("1", "Desenvolvedor Fullstack Pleno")]
    notifier = FakeNotifier()
    run_search(config, storage, [FakeSource(jobs)], scorer, [notifier])
    report2 = run_search(config, storage, [FakeSource(jobs)], scorer, [notifier])
    assert report2.new == 0 and report2.notified == 0
    assert len(notifier.sent) == 1


def test_dry_run_writes_nothing(env):
    config, storage, scorer = env
    notifier = FakeNotifier()
    report = run_search(config, storage,
                        [FakeSource([_job("1", "Dev Fullstack Pleno")])],
                        scorer, [notifier], dry_run=True)
    assert report.new == 1
    assert storage.list_jobs() == []
    assert notifier.sent == []


def test_blocked_source_sets_cooldown_and_skips_next_run(env):
    config, storage, scorer = env
    source = FakeSource([], blocked=True)
    report = run_search(config, storage, [source], scorer, [])
    assert report.errors
    assert storage.in_cooldown("fake")
    messages = []
    run_search(config, storage, [source], scorer, [],
               progress=messages.append)
    assert any("cooldown" in m for m in messages)


def test_posts_source_uses_own_queries(env):
    config, storage, scorer = env
    config.search.queries = ["query de vagas"]
    config.sources.linkedin_posts.queries = ["query de posts"]

    class NamedSource(FakeSource):
        def __init__(self, name):
            super().__init__([])
            self.name = name
            self.seen_queries = []

        def search(self, filters):
            self.seen_queries.append(filters.keywords)
            return iter([])

    jobs_src = NamedSource("linkedin_jobs")
    posts_src = NamedSource("linkedin_posts")
    run_search(config, storage, [jobs_src, posts_src], scorer, [])
    assert jobs_src.seen_queries == ["query de vagas"]
    assert posts_src.seen_queries == ["query de posts"]


def test_cancel_stops_search(env):
    config, storage, scorer = env
    jobs = [_job(str(i), f"Dev Fullstack {i}") for i in range(10)]
    stop_after = {"count": 0}

    def should_stop():
        stop_after["count"] += 1
        return stop_after["count"] > 3  # cancela no meio

    report = run_search(config, storage, [FakeSource(jobs)], scorer, [],
                        should_stop=should_stop)
    assert report.found < 10


def test_time_range_override_per_source(env):
    config, storage, scorer = env
    config.search.queries = ["dev"]
    config.search.time_range = "r2h"
    config.sources.infojobs.time_range = "r24h"

    class NamedSource(FakeSource):
        def __init__(self, name):
            super().__init__([])
            self.name = name
            self.ranges = []

        def search(self, filters):
            self.ranges.append(filters.time_range)
            return iter([])

    li = NamedSource("linkedin_jobs")
    ij = NamedSource("infojobs")
    run_search(config, storage, [li, ij], scorer, [])
    assert li.ranges == ["r2h"]
    assert ij.ranges == ["r24h"]
