import pytest
import requests

from vaga_hunter.http_client import PoliteSession, SourceBlockedError


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = ""


def make_session(responses, monkeypatch):
    session = PoliteSession(min_delay=0, max_delay=0, max_retries=2,
                            breaker_threshold=3)
    it = iter(responses)
    monkeypatch.setattr(session._session, "request",
                        lambda *a, **k: next(it))
    monkeypatch.setattr("time.sleep", lambda s: None)
    return session


def test_success_resets_streak(monkeypatch):
    session = make_session([FakeResponse(429), FakeResponse(200)], monkeypatch)
    resp = session.get("http://x")
    assert resp.status_code == 200
    assert session._block_streak == 0


def test_circuit_breaker_raises(monkeypatch):
    session = make_session([FakeResponse(429)] * 3, monkeypatch)
    with pytest.raises(SourceBlockedError):
        session.get("http://x")


def test_bounded_retries_then_error(monkeypatch):
    session = make_session([FakeResponse(500)] * 3, monkeypatch)
    with pytest.raises(requests.HTTPError):
        session.get("http://x")
