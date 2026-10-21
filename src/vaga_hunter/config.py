"""Carregamento e validação do config.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SearchConfig:
    queries: list[str] = field(default_factory=lambda: ["desenvolvedor fullstack"])
    location: str = "Brasil"
    time_range: str = "r24h"
    remote: str | None = None
    max_pages_per_query: int = 3


@dataclass
class ProfileConfig:
    roles: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=lambda: ["junior", "pleno"])
    exclude_keywords_hard: list[str] = field(default_factory=list)
    exclude_keywords_soft: list[str] = field(default_factory=list)
    include_keywords: list[str] = field(default_factory=list)


@dataclass
class ScoringConfig:
    threshold: float = 60.0
    weights: dict[str, float] = field(default_factory=lambda: {
        "role_match": 30, "seniority": 20, "keywords": 20, "remote": 15, "recency": 15,
    })


@dataclass
class PostsConfig:
    mode: str = "disabled"            # disabled | browser
    cdp_url: str = "http://127.0.0.1:9222"
    max_requests_per_run: int = 8


@dataclass
class SourcesConfig:
    linkedin_jobs: bool = True
    linkedin_posts: bool = False
    posts: PostsConfig = field(default_factory=PostsConfig)


@dataclass
class HttpConfig:
    min_delay: float = 2.0
    max_delay: float = 6.0
    max_retries: int = 3
    circuit_breaker_threshold: int = 3
    cooldown_hours: float = 6.0


@dataclass
class DaemonConfig:
    interval_minutes: int = 90
    jitter: float = 0.3
    window_start: str = "08:00"
    window_end: str = "20:00"
    weekdays_only: bool = True


@dataclass
class NotifyConfig:
    telegram: bool = True
    desktop: bool = True
    telegram_token: str = ""
    telegram_chat_id: str = ""


@dataclass
class Config:
    search: SearchConfig = field(default_factory=SearchConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    db_path: str = "vaga_hunter.db"
    base_dir: Path = field(default_factory=Path.cwd)
    config_path: Path | None = None


def load_config(path: str | Path | None = None) -> Config:
    """Carrega config.toml; campos ausentes usam defaults. Segredos vêm de env."""
    cfg = Config()
    if path is None:
        candidate = Path.cwd() / "config.toml"
        path = candidate if candidate.exists() else None
    if path is not None:
        path = Path(path)
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        cfg.base_dir = path.parent.resolve()
        cfg.config_path = path.resolve()
        _apply(cfg.search, raw.get("search", {}))
        _apply(cfg.profile, raw.get("profile", {}))
        scoring = raw.get("scoring", {})
        if "threshold" in scoring:
            cfg.scoring.threshold = float(scoring["threshold"])
        cfg.scoring.weights.update(scoring.get("weights", {}))
        sources = raw.get("sources", {})
        cfg.sources.linkedin_jobs = bool(sources.get("linkedin_jobs", True))
        cfg.sources.linkedin_posts = bool(sources.get("linkedin_posts", False))
        _apply(cfg.sources.posts, sources.get("posts", {}))
        _apply(cfg.http, raw.get("http", {}))
        _apply(cfg.daemon, raw.get("daemon", {}))
        _apply(cfg.notify, raw.get("notify", {}))
        cfg.db_path = raw.get("storage", {}).get("db_path", cfg.db_path)

    cfg.notify.telegram_token = os.environ.get("VAGA_HUNTER_TELEGRAM_TOKEN", "")
    cfg.notify.telegram_chat_id = os.environ.get("VAGA_HUNTER_TELEGRAM_CHAT_ID", "")
    return cfg


def save_search_params(cfg: Config) -> bool:
    """Persiste [search] e scoring.threshold no config.toml, preservando
    comentários e o resto do arquivo. Retorna False se não há arquivo."""
    if cfg.config_path is None or not cfg.config_path.exists():
        return False
    import tomlkit

    doc = tomlkit.parse(cfg.config_path.read_text())
    search = doc.setdefault("search", tomlkit.table())
    search["queries"] = cfg.search.queries
    search["location"] = cfg.search.location
    search["time_range"] = cfg.search.time_range
    search["remote"] = cfg.search.remote or ""
    search["max_pages_per_query"] = cfg.search.max_pages_per_query
    scoring = doc.setdefault("scoring", tomlkit.table())
    scoring["threshold"] = cfg.scoring.threshold
    cfg.config_path.write_text(tomlkit.dumps(doc))
    return True


def _apply(obj: object, data: dict) -> None:
    """Copia chaves conhecidas de um dict para um dataclass."""
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if hasattr(obj, key) and not isinstance(getattr(obj, key), (dict,)):
            setattr(obj, key, value)
