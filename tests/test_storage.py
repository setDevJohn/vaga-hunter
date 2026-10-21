from datetime import datetime, timezone

import pytest

from vaga_hunter.models import Job, JobStatus
from vaga_hunter.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def make_job(external_id="123", title="Dev Fullstack"):
    return Job(source="linkedin_jobs", external_id=external_id, title=title,
               company="Acme", location="Remoto",
               url="https://example.com/job/123",
               posted_at=datetime(2026, 10, 20, tzinfo=timezone.utc))


def test_upsert_new_then_dedup(storage):
    job = make_job()
    assert storage.upsert_job(job, 75.0, {"role_match": 30}) is True
    assert storage.exists(job.uid)
    # segunda vez: não é nova; score original é preservado (re-score de vaga
    # conhecida rodaria sem descrição e rebaixaria o valor indevidamente)
    assert storage.upsert_job(job, 10.0, {}) is False
    rows = storage.list_jobs()
    assert len(rows) == 1
    assert rows[0]["score"] == 75.0
    assert rows[0]["status"] == JobStatus.NEW
    assert rows[0]["last_seen_at"] >= rows[0]["first_seen_at"]


def test_list_filters(storage):
    storage.upsert_job(make_job("1", "A"), 90.0, {})
    storage.upsert_job(make_job("2", "B"), 40.0, {})
    high = storage.list_jobs(min_score=60)
    assert [r["title"] for r in high] == ["A"]


def test_set_status_and_mark_notified(storage):
    job = make_job()
    storage.upsert_job(job, 70.0, {})
    storage.mark_notified([job.uid])
    assert storage.list_jobs(status=JobStatus.NOTIFIED)[0]["uid"] == job.uid
    # mark_notified não sobrescreve status manual
    storage.set_status(job.uid, JobStatus.INTERESTED)
    storage.mark_notified([job.uid])
    assert storage.list_jobs(status=JobStatus.INTERESTED)[0]["uid"] == job.uid
    assert storage.set_status("inexistente", JobStatus.SEEN) is False


def test_cooldown(storage):
    assert storage.in_cooldown("linkedin_jobs") is False
    storage.set_cooldown("linkedin_jobs", hours=1)
    assert storage.in_cooldown("linkedin_jobs") is True
    storage.set_cooldown("linkedin_jobs", hours=-1)  # expirado
    assert storage.in_cooldown("linkedin_jobs") is False


def test_record_and_last_run(storage):
    storage.record_run("linkedin_jobs", "react", found=10, new=3)
    run = storage.last_run()
    assert run["found"] == 10 and run["new"] == 3 and run["query"] == "react"


def test_fingerprint_dedup_same_job_other_region(storage):
    job_recife = Job(source="linkedin_jobs", external_id="100",
                     title="Desenvolvedor React Frontend - Trabalho Remoto",
                     company="BairesDev", location="Recife e Região", url="https://x/100")
    job_salvador = Job(source="linkedin_jobs", external_id="200",
                       title="Desenvolvedor React Frontend - Trabalho Remoto",
                       company="BairesDev", location="Salvador e Região", url="https://x/200")
    assert storage.upsert_job(job_recife, 80.0, {}) is True
    # mesmo título+empresa, id diferente: duplicata — não insere nem é "nova"
    assert storage.upsert_job(job_salvador, 80.0, {}) is False
    assert len(storage.list_jobs()) == 1


def test_fingerprint_migration_backfills(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (uid TEXT PRIMARY KEY, source TEXT, external_id TEXT,
            title TEXT, company TEXT DEFAULT '', location TEXT DEFAULT '',
            url TEXT DEFAULT '', description TEXT DEFAULT '', posted_at TEXT,
            remote INTEGER, score REAL DEFAULT 0, score_details TEXT DEFAULT '{}',
            status TEXT DEFAULT 'new', first_seen_at TEXT, last_seen_at TEXT);
        INSERT INTO jobs (uid, source, external_id, title, company,
            first_seen_at, last_seen_at)
        VALUES ('linkedin_jobs:1', 'linkedin_jobs', '1', 'Dev X', 'Acme', 't', 't');
    """)
    conn.commit()
    conn.close()
    s = Storage(db)  # migração roda no init
    dup = Job(source="linkedin_jobs", external_id="2", title="Dev X", company="Acme")
    assert s.upsert_job(dup, 50.0, {}) is False  # pega dup da linha antiga
    s.close()
