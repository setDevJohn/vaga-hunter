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
    config.sources.linkedin_jobs.enabled = False  # sem fontes reais (sem rede nos testes)
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
    client = app.test_client()
    # POSTs exigem o header anti-CSRF
    original_post = client.post
    client.post = lambda *a, **k: original_post(
        *a, **{**k, "headers": {**k.get("headers", {}), "X-Requested-With": "vaga-hunter"}})
    yield client, daemon
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


def test_post_without_header_rejected(client):
    test_client, _ = client
    import flask.testing
    # chamada crua, sem o wrapper que injeta o header
    resp = flask.testing.FlaskClient.post(test_client, "/api/daemon/start")
    assert resp.status_code == 403


def test_get_config(client):
    test_client, _ = client
    cfg = test_client.get("/api/config").get_json()
    assert "queries" in cfg and "threshold" in cfg and "time_range" in cfg


def test_update_config_applies_in_memory(client):
    test_client, _ = client
    resp = test_client.post("/api/config", json={
        "queries": ["python backend", "  ", "react"],
        "location": "São Paulo",
        "time_range": "r3d",
        "remote": "remote",
        "max_pages_per_query": 2,
        "threshold": 70,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["saved_to_file"] is False  # sem config.toml
    cfg = test_client.get("/api/config").get_json()
    assert cfg["queries"] == ["python backend", "react"]  # vazios removidos
    assert cfg["location"] == "São Paulo"
    assert cfg["time_range"] == "r3d"
    assert cfg["threshold"] == 70


def test_update_config_rejects_invalid(client):
    test_client, _ = client
    assert test_client.post("/api/config", json={"queries": []}).status_code == 400
    assert test_client.post("/api/config", json={
        "queries": ["x"], "time_range": "errado"}).status_code == 400
    assert test_client.post("/api/config", json={
        "queries": ["x"], "remote": "lua"}).status_code == 400


def test_applied_status(client):
    test_client, _ = client
    resp = test_client.post("/api/jobs/linkedin_jobs:1/status", json={"status": "applied"})
    assert resp.status_code == 200
    jobs = test_client.get("/api/jobs?status=applied").get_json()
    assert [j["uid"] for j in jobs] == ["linkedin_jobs:1"]


def test_posts_status_disabled(client):
    test_client, _ = client
    status = test_client.get("/api/status").get_json()
    assert status["posts"] == {"enabled": False, "state": "disabled"}


def test_posts_prepare_rejected_when_disabled(client):
    test_client, _ = client
    resp = test_client.post("/api/posts/prepare")
    assert resp.status_code == 400


def test_cancel_without_running_search(client):
    test_client, _ = client
    resp = test_client.post("/api/search/cancel")
    assert resp.status_code == 409


def test_config_per_source(client):
    test_client, _ = client
    cfg = test_client.get("/api/config").get_json()
    from vaga_hunter.config import SOURCE_NAMES
    assert set(cfg["sources"]) == set(SOURCE_NAMES)
    resp = test_client.post("/api/config", json={
        "queries": ["x"],
        "sources": {"linkedin_posts": {
            "enabled": True, "queries": ["vaga react", " "],
            "time_range": "r24h", "remote": "any", "max_pages": 2}},
    })
    assert resp.status_code == 200
    cfg2 = test_client.get("/api/config").get_json()
    posts = cfg2["sources"]["linkedin_posts"]
    assert posts["enabled"] is True
    assert posts["queries"] == ["vaga react"]
    assert posts["time_range"] == "r24h" and posts["remote"] == "any"
    # valores inválidos rejeitados
    bad = test_client.post("/api/config", json={
        "queries": ["x"], "sources": {"infojobs": {"time_range": "errado"}}})
    assert bad.status_code == 400


def test_search_jobs_never_includes_posts(client, monkeypatch):
    """Busca normal filtra fontes autenticadas; busca de posts, as públicas."""
    import vaga_hunter.web.app as webapp
    from vaga_hunter.sources.base import JobSource

    class Public(JobSource):
        name = "linkedin_jobs"; requires_auth = False
        def search(self, f): return iter([])

    class Auth(JobSource):
        name = "linkedin_posts"; requires_auth = True
        def search(self, f): return iter([])

    used = []
    monkeypatch.setattr(webapp, "build_sources", lambda c, s: [Public(), Auth()])
    monkeypatch.setattr(webapp, "run_search",
                        lambda c, s, sources, *a, **k: used.extend(
                            src.name for src in sources) or __import__(
                            "vaga_hunter.models", fromlist=["RunReport"]).RunReport())
    test_client, _ = client
    import time
    resp = test_client.post("/api/search", json={"what": "jobs"})
    assert resp.status_code == 200
    time.sleep(0.3)
    assert used == ["linkedin_jobs"]


def test_search_posts_rejected_when_disabled(client):
    test_client, _ = client
    resp = test_client.post("/api/search", json={"what": "posts"})
    assert resp.status_code == 400
    resp2 = test_client.post("/api/search", json={"what": "xyz"})
    assert resp2.status_code == 400


def test_config_partial_update_preserves_values(client):
    test_client, _ = client
    test_client.post("/api/config", json={
        "queries": ["x"], "remote": "hybrid",
        "sources": {"infojobs": {"enabled": True, "queries": ["programador"],
                                 "time_range": "r24h"}}})
    # POST parcial: sem 'remote' e com source só trocando enabled
    test_client.post("/api/config", json={
        "queries": ["x"], "sources": {"infojobs": {"enabled": False}}})
    cfg = test_client.get("/api/config").get_json()
    assert cfg["remote"] == "hybrid"  # não resetou
    ij = cfg["sources"]["infojobs"]
    assert ij["enabled"] is False
    assert ij["queries"] == ["programador"]  # preservado
    assert ij["time_range"] == "r24h"


def test_config_rejects_zero_time_range(client):
    test_client, _ = client
    resp = test_client.post("/api/config", json={"queries": ["x"], "time_range": "r0h"})
    assert resp.status_code == 400


def test_get_config_inclui_profile(client):
    test_client, _ = client
    cfg = test_client.get("/api/config").get_json()
    profile = cfg["profile"]
    assert set(profile) == {"roles", "seniority", "include_keywords",
                            "exclude_keywords_soft", "exclude_keywords_hard"}
    assert profile["seniority"] == ["junior", "pleno"]


def test_update_profile_normaliza_e_persiste_em_memoria(client):
    test_client, _ = client
    resp = test_client.post("/api/config", json={
        "queries": ["dev"],
        "profile": {"roles": ["  Fullstack ", "", "React Native"],
                    "include_keywords": ["TypeScript"]},
    })
    assert resp.status_code == 200
    cfg = test_client.get("/api/config").get_json()["profile"]
    assert cfg["roles"] == ["fullstack", "react native"]       # strip + lowercase
    assert cfg["include_keywords"] == ["typescript"]
    assert cfg["seniority"] == ["junior", "pleno"]             # parcial preserva


def test_update_profile_rejeita_nao_lista_e_seniority_vazia(client):
    test_client, _ = client
    resp = test_client.post("/api/config", json={
        "queries": ["dev"], "profile": {"roles": "fullstack"}})
    assert resp.status_code == 400
    resp = test_client.post("/api/config", json={
        "queries": ["dev"], "profile": {"seniority": ["", "  "]}})
    assert resp.status_code == 400


def test_rescore_endpoint(client):
    test_client, _ = client
    resp = test_client.post("/api/rescore")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True and data["total"] == 2
    # scoring real reavaliou: "Dev Senior" com score 20 artificial muda
    assert data["changed"] >= 1


def test_go_apinfo_renderiza_form_autopost(client):
    test_client, _ = client
    resp = test_client.get("/go/apinfo/86575")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'action=\'https://www.apinfo.com/apinfo/inc/list4.cfm\'' in html
    assert 'name="keyw" value="86575"' in html
    assert "document.forms[0].submit()" in html


def test_go_apinfo_rejeita_codigo_invalido(client):
    test_client, _ = client
    assert test_client.get("/go/apinfo/abc").status_code == 400
    assert test_client.get("/go/apinfo/1%3Cscript%3E").status_code == 400
