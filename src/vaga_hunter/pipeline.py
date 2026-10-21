"""Orquestração: fontes -> dedup -> score -> persistência -> notificação."""
from __future__ import annotations

import logging
from collections.abc import Callable

from .config import Config
from .http_client import PoliteSession, SourceBlockedError
from .models import Job, RunReport, SearchFilters
from .scoring import Scorer
from .sources.base import JobSource
from .sources.linkedin_jobs import LinkedInJobsSource
from .storage import Storage

log = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


def build_sources(config: Config, storage: Storage) -> list[JobSource]:
    sources: list[JobSource] = []
    if config.sources.linkedin_jobs:
        session = PoliteSession(
            min_delay=config.http.min_delay,
            max_delay=config.http.max_delay,
            max_retries=config.http.max_retries,
            breaker_threshold=config.http.circuit_breaker_threshold,
        )
        sources.append(LinkedInJobsSource(session, skip_detail=storage.exists))
    if config.sources.linkedin_posts and config.sources.posts.mode == "browser":
        from .sources.linkedin_posts import LinkedInPostsSource
        sources.append(LinkedInPostsSource(config.sources.posts))
    return sources


def build_filters(config: Config) -> list[SearchFilters]:
    return [
        SearchFilters(
            keywords=query,
            location=config.search.location,
            time_range=config.search.time_range,
            remote=config.search.remote or None,
            max_pages=config.search.max_pages_per_query,
        )
        for query in config.search.queries
    ]


def run_search(config: Config, storage: Storage, sources: list[JobSource],
               scorer: Scorer, notifiers: list, *, dry_run: bool = False,
               progress: ProgressFn = lambda msg: None) -> RunReport:
    report = RunReport()
    to_notify: list[tuple[Job, float]] = []

    for source in sources:
        if storage.in_cooldown(source.name):
            progress(f"[{source.name}] em cooldown — pulando")
            continue
        for filters in build_filters(config):
            progress(f"[{source.name}] buscando: {filters.keywords}")
            found = new = 0
            error: str | None = None
            try:
                for job in source.search(filters):
                    found += 1
                    score, details = scorer.score(job)
                    if dry_run:
                        if not storage.exists(job.uid):
                            new += 1
                        continue
                    if storage.upsert_job(job, score, details):
                        new += 1
                        if score >= config.scoring.threshold:
                            to_notify.append((job, score))
            except SourceBlockedError as exc:
                error = str(exc)
                storage.set_cooldown(source.name, config.http.cooldown_hours)
                progress(f"[{source.name}] BLOQUEIO detectado: {exc}. "
                         f"Cooldown de {config.http.cooldown_hours}h ativado.")
                report.errors.append(f"{source.name}: {exc}")
            except Exception as exc:
                error = str(exc)
                log.exception("erro na fonte %s", source.name)
                report.errors.append(f"{source.name}: {exc}")

            report.found += found
            report.new += new
            if not dry_run:
                storage.record_run(source.name, filters.keywords, found, new, error)
            progress(f"[{source.name}] {filters.keywords}: {found} vagas, {new} novas")
            if error and "consecutivas" in error:
                break  # circuit breaker: não insiste nas próximas queries

    if to_notify and not dry_run:
        to_notify.sort(key=lambda item: item[1], reverse=True)
        jobs = [job for job, _ in to_notify]
        for notifier in notifiers:
            try:
                notifier.notify(jobs)
            except Exception as exc:
                log.exception("falha no notificador %s", type(notifier).__name__)
                report.errors.append(f"notify: {exc}")
        storage.mark_notified([job.uid for job in jobs])
        report.notified = len(jobs)

    return report
