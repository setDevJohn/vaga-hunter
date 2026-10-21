"""Sessão HTTP "educada": delays aleatórios, rotação de UA, backoff, circuit breaker."""
from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

BLOCK_STATUS = {429, 999}


class SourceBlockedError(Exception):
    """A fonte respondeu com bloqueio/rate-limit repetidas vezes."""


class PoliteSession:
    """Wrapper de requests.Session com comportamento conservador anti-bloqueio.

    - User-Agent sorteado por sessão (não por request — mais natural).
    - Delay aleatório uniform(min_delay, max_delay) antes de cada request.
    - Retries limitados com backoff exponencial + jitter (sem recursão).
    - Circuit breaker: N respostas de bloqueio consecutivas -> SourceBlockedError.
    """

    def __init__(self, min_delay: float = 2.0, max_delay: float = 6.0,
                 max_retries: int = 3, breaker_threshold: int = 3,
                 timeout: float = 20.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.breaker_threshold = breaker_threshold
        self.timeout = timeout
        self._block_streak = 0
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def _sleep(self, base: float | None = None) -> None:
        time.sleep(base if base is not None else random.uniform(self.min_delay, self.max_delay))

    def get(self, url: str, params: dict | None = None) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt == 0:
                self._sleep()
            else:
                backoff = (2 ** attempt) + random.uniform(0, 1.5)
                log.debug("retry %d em %.1fs: %s", attempt, backoff, url)
                self._sleep(backoff)
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                continue

            if resp.status_code in BLOCK_STATUS:
                self._block_streak += 1
                log.warning("resposta %d (streak=%d): %s",
                            resp.status_code, self._block_streak, url)
                if self._block_streak >= self.breaker_threshold:
                    raise SourceBlockedError(
                        f"{self._block_streak} respostas {resp.status_code} consecutivas")
                last_error = requests.HTTPError(f"status {resp.status_code}")
                continue

            if resp.ok:
                self._block_streak = 0
                return resp
            last_error = requests.HTTPError(f"status {resp.status_code}")

        raise requests.HTTPError(f"falha após {self.max_retries + 1} tentativas: {url}") \
            from last_error
