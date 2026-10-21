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
    """True se `now` está dentro da janela permitida de buscas.

    Suporta janelas que cruzam a meia-noite (ex.: 22:00-06:00).
    """
    if cfg.weekdays_only and now.weekday() >= 5:
        return False
    start = _parse_hhmm(cfg.window_start)
    end = _parse_hhmm(cfg.window_end)
    if start <= end:
        return start <= now.time() <= end
    return now.time() >= start or now.time() <= end


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
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_run: datetime | None = None
        self.next_run: datetime | None = None
        self.last_report: dict | None = None

    # --- run -------------------------------------------------------------

    def _default_run(self) -> object:
        from .notify import build_notifiers
        # Daemon NUNCA usa fontes autenticadas (posts): rodam só em busca
        # manual, com o usuário presente — proteção da conta.
        sources = [s for s in build_sources(self.config, self.storage)
                   if not s.requires_auth]
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
            # Event próprio por geração: um start novo nunca "rouba" o stop
            # de uma thread antiga que ainda não terminou o join.
            stop = threading.Event()
            self._stop = stop
            self._thread = threading.Thread(target=self._loop, args=(stop,),
                                            daemon=True, name="vaga-hunter-daemon")
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.running:
                return False
            self._stop.set()
            self._thread.join(timeout=5)
            if not self._thread.is_alive():
                self._thread = None
            self.next_run = None
            return True

    def _loop(self, stop: threading.Event) -> None:
        log.info("daemon iniciado: intervalo %d min (±%d%%), janela %s-%s",
                 self.config.daemon.interval_minutes,
                 int(self.config.daemon.jitter * 100),
                 self.config.daemon.window_start, self.config.daemon.window_end)
        while not stop.is_set():
            try:
                now = self.clock()
                if in_window(self.config.daemon, now):
                    self.run_once()
                    wait = next_interval_seconds(self.config.daemon)
                else:
                    wait = 300.0  # fora da janela: reavalia em 5 min
            except Exception as exc:
                log.exception("erro no loop do daemon")
                self.last_report = {"errors": [str(exc)]}
                wait = 300.0
            self.next_run = self.clock() + timedelta(seconds=wait)
            if stop.wait(timeout=wait):
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
