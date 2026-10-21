import random
import time
from datetime import datetime

from vaga_hunter.config import Config, DaemonConfig
from vaga_hunter.daemon import DaemonController, in_window, next_interval_seconds
from vaga_hunter.storage import Storage

CFG = DaemonConfig(interval_minutes=90, jitter=0.3,
                   window_start="08:00", window_end="20:00", weekdays_only=True)


def test_in_window_weekday_inside():
    assert in_window(CFG, datetime(2026, 10, 21, 10, 0))  # quarta 10h


def test_out_of_window_hours():
    assert not in_window(CFG, datetime(2026, 10, 21, 7, 59))
    assert not in_window(CFG, datetime(2026, 10, 21, 20, 1))


def test_out_of_window_weekend():
    assert not in_window(CFG, datetime(2026, 10, 24, 10, 0))  # sábado


def test_weekend_allowed_when_configured():
    cfg = DaemonConfig(weekdays_only=False)
    assert in_window(cfg, datetime(2026, 10, 24, 10, 0))


def test_next_interval_jitter_bounds():
    rand = random.Random(42)
    for _ in range(100):
        seconds = next_interval_seconds(CFG, rand)
        assert 90 * 60 * 0.7 <= seconds <= 90 * 60 * 1.3


def test_controller_start_stop_and_runs(tmp_path):
    config = Config()
    config.daemon.interval_minutes = 1
    storage = Storage(tmp_path / "d.db")
    calls = []
    controller = DaemonController(
        config, storage,
        clock=lambda: datetime(2026, 10, 21, 10, 0),
        run_fn=lambda: calls.append(1),
    )
    assert controller.start() is True
    assert controller.start() is False  # já rodando
    deadline = time.time() + 2
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    assert calls  # executou ao menos uma busca
    assert controller.running
    assert controller.stop() is True
    assert controller.stop() is False
    status = controller.status()
    assert status["running"] is False
    assert status["last_run"] is not None
    storage.close()


def test_window_wrapping_midnight():
    cfg = DaemonConfig(window_start="22:00", window_end="06:00", weekdays_only=False)
    assert in_window(cfg, datetime(2026, 10, 21, 23, 0))
    assert in_window(cfg, datetime(2026, 10, 21, 5, 0))
    assert not in_window(cfg, datetime(2026, 10, 21, 12, 0))


def test_stop_does_not_cancel_new_generation(tmp_path):
    """stop() de uma geração não pode afetar um start() posterior."""
    import threading
    config = Config()
    storage = Storage(tmp_path / "g.db")
    release = threading.Event()
    controller = DaemonController(
        config, storage,
        clock=lambda: datetime(2026, 10, 21, 10, 0),
        run_fn=lambda: release.wait(timeout=8),  # busca "lenta"
    )
    controller.start()
    time.sleep(0.1)
    first_stop_event = controller._stop
    controller.stop()  # join expira (run_fn preso), thread antiga segue viva
    release.set()      # libera a thread antiga terminar
    time.sleep(0.3)
    assert controller.start() is True
    # o novo start tem Event próprio; o stop antigo não o afeta
    assert controller._stop is not first_stop_event
    assert not controller._stop.is_set()
    controller.stop()
    storage.close()


def test_daemon_skips_auth_sources(tmp_path, monkeypatch):
    """Daemon nunca roda fontes autenticadas (posts)."""
    from vaga_hunter import daemon as daemon_mod
    from vaga_hunter.sources.base import JobSource

    class FakeAuth(JobSource):
        name = "linkedin_posts"
        requires_auth = True
        def search(self, filters): return iter([])

    class FakePublic(JobSource):
        name = "linkedin_jobs"
        requires_auth = False
        def search(self, filters): return iter([])

    used = []
    monkeypatch.setattr(daemon_mod, "build_sources",
                        lambda c, s: [FakePublic(), FakeAuth()])
    monkeypatch.setattr(daemon_mod, "run_search",
                        lambda c, s, sources, *a, **k: used.extend(
                            src.name for src in sources))
    config = Config()
    config.notify.telegram = False
    config.notify.desktop = False
    storage = Storage(tmp_path / "a.db")
    controller = DaemonController(config, storage)
    controller.run_once()
    assert used == ["linkedin_jobs"]
    storage.close()
