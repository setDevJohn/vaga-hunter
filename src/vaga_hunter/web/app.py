"""Interface web: busca manual, toggle do daemon, triagem das vagas."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

import requests as _requests

from flask import Flask, jsonify, render_template, request

from ..config import Config, save_search_params
from ..daemon import DaemonController
from ..models import JobStatus
from ..pipeline import build_sources, run_search
from ..scoring import Scorer, detect_modality
from ..storage import Storage

log = logging.getLogger(__name__)

VALID_STATUSES = {s.value for s in JobStatus}


class SearchState:
    """Estado da busca manual em andamento (uma por vez)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.messages: list[str] = []
        self.report: dict | None = None

    def log(self, msg: str) -> None:
        self.messages.append(msg)


def create_app(config: Config, storage: Storage | None = None,
               daemon: DaemonController | None = None) -> Flask:
    app = Flask(__name__)
    db_path = Path(config.db_path)
    if not db_path.is_absolute():
        db_path = config.base_dir / db_path
    storage = storage or Storage(db_path)
    daemon = daemon or DaemonController(config, storage)
    state = SearchState()

    def _manual_search() -> None:
        from ..notify import build_notifiers
        try:
            sources = build_sources(config, storage)
            scorer = Scorer(config.profile, config.scoring)
            notifiers = build_notifiers(config)
            report = run_search(config, storage, sources, scorer, notifiers,
                                progress=state.log)
            state.report = {"found": report.found, "new": report.new,
                            "notified": report.notified, "errors": report.errors}
            state.log(f"Concluído: {report.found} vagas, {report.new} novas, "
                      f"{report.notified} notificadas.")
        except Exception as exc:
            log.exception("erro na busca manual")
            state.log(f"Erro: {exc}")
        finally:
            state.running = False

    @app.before_request
    def _csrf_guard():
        # POSTs exigem header customizado: força preflight CORS e impede que
        # qualquer página aberta no navegador dispare buscas via 127.0.0.1.
        if request.method == "POST" and \
                request.headers.get("X-Requested-With") != "vaga-hunter":
            return jsonify({"ok": False, "error": "header X-Requested-With ausente"}), 403

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/api/search")
    def api_search():
        with state.lock:
            if state.running:
                return jsonify({"ok": False, "error": "busca já em andamento"}), 409
            state.running = True
            state.messages = []
            state.report = None
        threading.Thread(target=_manual_search, daemon=True).start()
        return jsonify({"ok": True})

    def _cdp_ok() -> bool:
        try:
            return _requests.get(f"{config.sources.posts.cdp_url}/json/version",
                                 timeout=1.5).ok
        except _requests.RequestException:
            return False

    def _posts_status() -> dict:
        enabled = config.sources.linkedin_posts and config.sources.posts.mode == "browser"
        if not enabled:
            return {"enabled": False, "state": "disabled"}
        return {"enabled": True, "state": "ready" if _cdp_ok() else "chrome_closed"}

    @app.get("/api/status")
    def api_status():
        return jsonify({
            "search": {"running": state.running, "messages": state.messages[-50:],
                       "report": state.report},
            "daemon": daemon.status(),
            "counts": storage.count_by_status(),
            "posts": _posts_status(),
        })

    @app.post("/api/posts/prepare")
    def api_posts_prepare():
        """Deixa o Chrome pronto pra busca de posts.

        Chrome 136+ ignora --remote-debugging-port no perfil padrão, então o
        bot usa um perfil próprio e persistente (--user-data-dir). Vantagem:
        funciona mesmo com o Chrome pessoal aberto. No primeiro uso é preciso
        logar no LinkedIn nesse perfil uma única vez.
        """
        status = _posts_status()
        if not status["enabled"]:
            return jsonify({"ok": False, "error": "fonte de posts desligada no config"}), 400
        if status["state"] == "ready":
            return jsonify({"ok": True, "state": "ready"})
        chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not chrome:
            return jsonify({"ok": False, "error": "google-chrome não encontrado"}), 500
        port = config.sources.posts.cdp_url.rsplit(":", 1)[-1]
        profile_dir = Path.home() / ".config" / "vaga-hunter-chrome"
        profile_dir.mkdir(parents=True, exist_ok=True)
        first_use = not (profile_dir / "Default").exists()
        subprocess.Popen(
            [chrome, f"--remote-debugging-port={port}",
             f"--user-data-dir={profile_dir}",
             "--no-first-run", "--no-default-browser-check",
             "https://www.linkedin.com/feed/"],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):  # até ~10s pro Chrome subir
            time.sleep(0.5)
            if _cdp_ok():
                return jsonify({"ok": True, "state": "ready", "launched": True,
                                "first_use": first_use})
        return jsonify({"ok": False, "error": "Chrome não respondeu na porta CDP"}), 500

    @app.get("/api/jobs")
    def api_jobs():
        status = request.args.get("status") or None
        min_score = request.args.get("min_score", type=float)
        rows = storage.list_jobs(status=status, min_score=min_score,
                                 limit=request.args.get("limit", 200, type=int))
        jobs = []
        for row in rows:
            try:
                details = json.loads(row["score_details"] or "{}")
            except (ValueError, TypeError):
                details = {}
            jobs.append({
                "uid": row["uid"], "source": row["source"],
                "title": row["title"], "company": row["company"],
                "location": row["location"], "url": row["url"],
                "description": (row["description"] or "")[:800],
                "posted_at": row["posted_at"], "remote": row["remote"],
                "modality": detect_modality(row["title"], row["description"] or ""),
                "score": row["score"], "score_details": details,
                "status": row["status"], "first_seen_at": row["first_seen_at"],
            })
        return jsonify(jobs)

    @app.post("/api/jobs/<path:uid>/status")
    def api_job_status(uid: str):
        new_status = (request.get_json(silent=True) or {}).get("status", "")
        if new_status not in VALID_STATUSES:
            return jsonify({"ok": False, "error": "status inválido"}), 400
        if not storage.set_status(uid, new_status):
            return jsonify({"ok": False, "error": "vaga não encontrada"}), 404
        return jsonify({"ok": True})

    @app.get("/api/config")
    def api_config_get():
        return jsonify({
            "queries": config.search.queries,
            "location": config.search.location,
            "time_range": config.search.time_range,
            "remote": config.search.remote or "",
            "max_pages_per_query": config.search.max_pages_per_query,
            "threshold": config.scoring.threshold,
        })

    @app.post("/api/config")
    def api_config_set():
        data = request.get_json(silent=True) or {}
        errors = []

        queries = [q.strip() for q in data.get("queries", []) if str(q).strip()]
        if not queries:
            errors.append("informe pelo menos uma busca")
        import re as _re
        time_range = data.get("time_range", config.search.time_range)
        if time_range not in {"r24h", "r3d", "r7d", "r1m"} and \
                not _re.match(r"^r\d+(m|h|d)$", time_range):
            errors.append("time_range inválido (use r30m, r1h, r2h, r24h, r3d...)")
        remote = data.get("remote", "")
        if remote not in {"", "remote", "hybrid", "onsite"}:
            errors.append("remote inválido")
        try:
            max_pages = max(1, min(10, int(data.get(
                "max_pages_per_query", config.search.max_pages_per_query))))
            threshold = max(0.0, min(100.0, float(data.get(
                "threshold", config.scoring.threshold))))
        except (TypeError, ValueError):
            errors.append("número inválido")
        if errors:
            return jsonify({"ok": False, "error": "; ".join(errors)}), 400

        config.search.queries = queries
        config.search.location = str(data.get("location", config.search.location)).strip() or "Brasil"
        config.search.time_range = time_range
        config.search.remote = remote or None
        config.search.max_pages_per_query = max_pages
        config.scoring.threshold = threshold
        saved = save_search_params(config)  # vale na hora; persiste se há config.toml
        return jsonify({"ok": True, "saved_to_file": saved})

    @app.post("/api/daemon/start")
    def api_daemon_start():
        started = daemon.start()
        return jsonify({"ok": True, "started": started, **daemon.status()})

    @app.post("/api/daemon/stop")
    def api_daemon_stop():
        stopped = daemon.stop()
        return jsonify({"ok": True, "stopped": stopped, **daemon.status()})

    return app
