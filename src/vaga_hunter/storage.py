"""Persistência em SQLite: vagas, execuções e metadados."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Job, JobStatus


def job_fingerprint(title: str, company: str) -> str:
    """Identidade semântica da vaga: mesma vaga repostada com outro id
    (ex.: por região) tem o mesmo fingerprint."""
    def norm(text: str) -> str:
        text = unicodedata.normalize("NFKD", text.lower())
        text = "".join(c for c in text if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]+", " ", text).strip()
    return f"{norm(title)}|{norm(company)}"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT DEFAULT '',
    location      TEXT DEFAULT '',
    url           TEXT DEFAULT '',
    description   TEXT DEFAULT '',
    posted_at     TEXT,
    remote        INTEGER,
    score         REAL DEFAULT 0,
    score_details TEXT DEFAULT '{}',
    status        TEXT DEFAULT 'new',
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    fingerprint   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    source      TEXT,
    query       TEXT,
    found       INTEGER DEFAULT 0,
    new         INTEGER DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        # Conexão única compartilhada entre threads (Flask/daemon/busca manual):
        # todo acesso passa pelo RLock para serializar statement+commit.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                     timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        cols = {row["name"] for row in
                self._conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "fingerprint" not in cols:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN fingerprint TEXT")
            for row in self._conn.execute(
                    "SELECT uid, title, company FROM jobs").fetchall():
                self._conn.execute(
                    "UPDATE jobs SET fingerprint = ? WHERE uid = ?",
                    (job_fingerprint(row["title"], row["company"]), row["uid"]))
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint)")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- jobs -----------------------------------------------------------

    def exists(self, uid: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM jobs WHERE uid = ?", (uid,)).fetchone()
        return row is not None

    def upsert_job(self, job: Job, score: float, details: dict[str, float]) -> bool:
        """Insere a vaga se nova; se já existe, só atualiza `last_seen_at`.

        Score e descrição de vagas conhecidas são preservados (buscas seguintes
        pulam o fetch da descrição e re-pontuariam com dados incompletos).
        Retorna True se a vaga é nova.
        """
        now = _now()
        fingerprint = job_fingerprint(job.title, job.company)
        with self._lock:  # check-then-insert atômico: todo acesso passa pelo lock
            row = self._conn.execute(
                "SELECT 1 FROM jobs WHERE uid = ?", (job.uid,)).fetchone()
            if row is not None:
                self._conn.execute(
                    "UPDATE jobs SET last_seen_at = ? WHERE uid = ?", (now, job.uid))
                self._conn.commit()
                return False
            # Mesmo título+empresa com outro id (repost por região): duplicata —
            # não insere nem notifica, só marca a original como vista de novo.
            dup = self._conn.execute(
                "SELECT uid FROM jobs WHERE fingerprint = ? LIMIT 1",
                (fingerprint,)).fetchone()
            if dup is not None:
                self._conn.execute(
                    "UPDATE jobs SET last_seen_at = ? WHERE uid = ?",
                    (now, dup["uid"]))
                self._conn.commit()
                return False
            self._conn.execute(
                """INSERT INTO jobs (uid, source, external_id, title, company,
                       location, url, description, posted_at, remote, score,
                       score_details, status, first_seen_at, last_seen_at, fingerprint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job.uid, job.source, job.external_id, job.title, job.company,
                 job.location, job.url, job.description,
                 job.posted_at.isoformat() if job.posted_at else None,
                 None if job.remote is None else int(job.remote),
                 score, json.dumps(details), JobStatus.NEW, now, now, fingerprint),
            )
            self._conn.commit()
        return True

    def list_jobs(self, status: str | None = None, min_score: float | None = None,
                  limit: int = 200) -> list[sqlite3.Row]:
        query = "SELECT * FROM jobs"
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if min_score is not None:
            clauses.append("score >= ?")
            params.append(min_score)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY score DESC, first_seen_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self._conn.execute(query, params).fetchall()

    def set_status(self, uid: str, status: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ? WHERE uid = ?", (status, uid))
            self._conn.commit()
        return cur.rowcount > 0

    def update_score(self, uid: str, score: float, details: dict[str, float]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET score = ?, score_details = ? WHERE uid = ?",
                (score, json.dumps(details), uid))
            self._conn.commit()

    def count_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    def mark_notified(self, uids: list[str]) -> None:
        with self._lock:
            self._conn.executemany(
                "UPDATE jobs SET status = ? WHERE uid = ? AND status = ?",
                [(JobStatus.NOTIFIED, uid, JobStatus.NEW) for uid in uids],
            )
            self._conn.commit()

    # --- runs -----------------------------------------------------------

    def record_run(self, source: str, query: str, found: int, new: int,
                   error: str | None = None, started_at: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (started_at, finished_at, source, query, found, new, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (started_at or _now(), _now(), source, query, found, new, error),
            )
            self._conn.commit()

    def last_run(self) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    # --- meta / cooldown ------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def set_cooldown(self, source: str, hours: float) -> None:
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        self.set_meta(f"cooldown:{source}", until.isoformat())

    def in_cooldown(self, source: str) -> bool:
        value = self.get_meta(f"cooldown:{source}")
        if not value:
            return False
        return datetime.now(timezone.utc) < datetime.fromisoformat(value)
