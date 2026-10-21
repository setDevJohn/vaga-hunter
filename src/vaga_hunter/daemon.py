"""Daemon de buscas periódicas: intervalo com jitter + janela horária."""
from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from datetime import datetime, time as dtime, timedelta

from .config import Config, DaemonConfig
from .pipeline import build_sources, run_search
from .scoring import Scorer
from .storage import Storage

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def _parse_hhmm(value: str) -> dtime:
    hour, minute = value.split(":")
    return dtime(int(hour), int(minute))


def in_window(cfg: DaemonConfig, now: datetime) -> bool:
    """True se `now` está dentro da janela permitida de buscas."""
    if cfg.weekdays_only and now.weekday() >= 5:
        return False
    return _parse_hhmm(cfg.window_start) <= now.time() <= _parse_hhmm(cfg.window_end)


def next_interval_seconds(cfg: DaemonConfig, rand: random.Random | None = None) -> float:
    """Intervalo com jitter: interval * uniform(1-jitter, 1+jitter)."""
    rand = rand or random
    factor = rand.uniform(1 - cfg.jitter, 1 + cfg.jitter)
    return max(60.0, cfg.interval_minutes * 60 * factor)


class DaemonController:
    """Controla a thread de buscas periódicas (start/stop/status)."""

    def __init__(self, config: Config, storage: Storage,
                 clock: Clock | None = None,
                 run_fn: Callable[..., object] | None = None):
        self.config = config
        self.storage = storage
        self.clock = clock or datetime.now
        self._run_fn = run_fn or self._default_run
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_run: datetime | None = None
        self.next_run: datetime | None = None
        self.last_report: dict | None = None

    # --- run -------------------------------------------------------------

    def _default_run(self) -> object:
        from .notify import build_notifiers
        sources = build_sources(self.config, self.storage)
        scorer = Scorer(self.config.profile, self.config.scoring)
        notifiers = build_notifiers(self.config)
        return run_search(self.config, self.storage, sources, scorer, notifiers,
                          progress=lambda msg: log.info("%s", msg))

    def run_once(self) -> None:
        self.last_run = self.clock()
        try:
            report = self._run_fn()
            self.last_report = {
                "found": getattr(report, "found", None),
                "new": getattr(report, "new", None),
                "notified": getattr(report, "notified", None),
                "errors": getattr(report, "errors", []),
            }
        except Exception as exc:
            log.exception("erro na busca periódica")
            self.last_report = {"errors": [str(exc)]}

    # --- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="vaga-hunter-daemon")
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.running:
                return False
            self._stop.set()
            self._thread.join(timeout=5)
            self._thread = None
            self.next_run = None
            return True

    def _loop(self) -> None:
        log.info("daemon iniciado: intervalo %d min (±%d%%), janela %s-%s",
                 self.config.daemon.interval_minutes,
                 int(self.config.daemon.jitter * 100),
                 self.config.daemon.window_start, self.config.daemon.window_end)
        while not self._stop.is_set():
            now = self.clock()
            if in_window(self.config.daemon, now):
                self.run_once()
                wait = next_interval_seconds(self.config.daemon)
            else:
                wait = 300.0  # fora da janela: reavalia em 5 min
            self.next_run = self.clock() + timedelta(seconds=wait)
            if self._stop.wait(timeout=wait):
                break
        log.info("daemon encerrado")

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "last_report": self.last_report,
            "in_window": in_window(self.config.daemon, self.clock()),
        }
