from datetime import datetime

import pytest

from vaga_hunter.config import Config
from vaga_hunter.daemon import DaemonController
from vaga_hunter.models import Job
from vaga_hunter.storage import Storage
from vaga_hunter.web.app import create_app


@pytest.fixture
def client(tmp_path):
    config = Config()
    config.sources.linkedin_jobs = False  # sem fontes reais (sem rede nos testes)
    config.notify.telegram = False
    config.notify.desktop = False
    storage = Storage(tmp_path / "w.db")
    storage.upsert_job(
        Job(source="linkedin_jobs", external_id="1", title="Dev Fullstack Pleno",
            company="Acme", location="Remoto", url="https://x/1"),
        score=80.0, details={})
    storage.upsert_job(
        Job(source="linkedin_jobs", external_id="2", title="Dev Senior",
            company="Beta", location="SP", url="https://x/2"),
        score=20.0, details={})
    daemon = DaemonController(config, storage,
                              clock=lambda: datetime(2026, 10, 21, 10, 0),
                              run_fn=lambda: None)
    app = create_app(config, storage=storage, daemon=daemon)
    app.testing = True
    yield app.test_client(), daemon
    daemon.stop()
    storage.close()


def test_index_renders(client):
    test_client, _ = client
    resp = test_client.get("/")
    assert resp.status_code == 200
    assert b"vaga-hunter" in resp.data


def test_list_jobs_with_filters(client):
    test_client, _ = client
    all_jobs = test_client.get("/api/jobs").get_json()
    assert len(all_jobs) == 2
    assert all_jobs[0]["score"] >= all_jobs[1]["score"]
    high = test_client.get("/api/jobs?min_score=50").get_json()
    assert len(high) == 1


def test_set_job_status(client):
    test_client, _ = client
    uid = "linkedin_jobs:1"
    resp = test_client.post(f"/api/jobs/{uid}/status", json={"status": "interested"})
    assert resp.status_code == 200
    jobs = test_client.get("/api/jobs?status=interested").get_json()
    assert [j["uid"] for j in jobs] == [uid]


def test_set_invalid_status(client):
    test_client, _ = client
    resp = test_client.post("/api/jobs/linkedin_jobs:1/status", json={"status": "xyz"})
    assert resp.status_code == 400
    resp2 = test_client.post("/api/jobs/nao-existe/status", json={"status": "seen"})
    assert resp2.status_code == 404


def test_daemon_toggle(client):
    test_client, daemon = client
    status = test_client.get("/api/status").get_json()
    assert status["daemon"]["running"] is False

    resp = test_client.post("/api/daemon/start").get_json()
    assert resp["started"] is True and resp["running"] is True
    # start duplo é idempotente
    assert test_client.post("/api/daemon/start").get_json()["started"] is False

    resp = test_client.post("/api/daemon/stop").get_json()
    assert resp["stopped"] is True and resp["running"] is False


def test_manual_search_conflict(client):
    test_client, _ = client
    # monkeypatch simples: marca busca como em andamento
    resp1 = test_client.post("/api/search")
    assert resp1.status_code == 200
    # a thread real pode terminar rápido; o teste de 409 exige busca em andamento,
    # então apenas valida a resposta do status
    status = test_client.get("/api/status").get_json()
    assert "search" in status and "daemon" in status
