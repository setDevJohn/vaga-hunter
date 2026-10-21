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
