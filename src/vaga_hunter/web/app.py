"""Interface web: busca manual, toggle do daemon, triagem das vagas."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ..config import Config
from ..daemon import DaemonController
from ..models import JobStatus
from ..pipeline import build_sources, run_search
from ..scoring import Scorer
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

    @app.get("/api/status")
    def api_status():
        return jsonify({
            "search": {"running": state.running, "messages": state.messages[-50:],
                       "report": state.report},
            "daemon": daemon.status(),
        })

    @app.get("/api/jobs")
    def api_jobs():
        status = request.args.get("status") or None
        min_score = request.args.get("min_score", type=float)
        rows = storage.list_jobs(status=status, min_score=min_score,
                                 limit=request.args.get("limit", 200, type=int))
        return jsonify([dict(row) for row in rows])

    @app.post("/api/jobs/<path:uid>/status")
    def api_job_status(uid: str):
        new_status = (request.get_json(silent=True) or {}).get("status", "")
        if new_status not in VALID_STATUSES:
            return jsonify({"ok": False, "error": "status inválido"}), 400
        if not storage.set_status(uid, new_status):
            return jsonify({"ok": False, "error": "vaga não encontrada"}), 404
        return jsonify({"ok": True})

    @app.post("/api/daemon/start")
    def api_daemon_start():
        started = daemon.start()
        return jsonify({"ok": True, "started": started, **daemon.status()})

    @app.post("/api/daemon/stop")
    def api_daemon_stop():
        stopped = daemon.stop()
        return jsonify({"ok": True, "stopped": stopped, **daemon.status()})

    return app
