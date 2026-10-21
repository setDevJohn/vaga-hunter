"""Orquestração: fontes -> dedup -> score -> persistência -> notificação."""
from __future__ import annotations

import logging
import threading
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

# Serializa execuções concorrentes (busca manual x daemon no mesmo processo):
# evita dupla inserção/notificação da mesma vaga.
_RUN_LOCK = threading.Lock()


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


def build_filters(config: Config, source: JobSource | None = None) -> list[SearchFilters]:
    queries = config.search.queries
    if source is not None and source.name == "linkedin_posts" and config.sources.posts.queries:
        queries = config.sources.posts.queries  # queries próprias da busca em posts
    return [
        SearchFilters(
            keywords=query,
            location=config.search.location,
            time_range=config.search.time_range,
            remote=config.search.remote or None,
            max_pages=config.search.max_pages_per_query,
        )
        for query in queries
    ]


def run_search(config: Config, storage: Storage, sources: list[JobSource],
               scorer: Scorer, notifiers: list, *, dry_run: bool = False,
               progress: ProgressFn = lambda msg: None,
               should_stop: Callable[[], bool] = lambda: False) -> RunReport:
    with _RUN_LOCK:
        return _run_search(config, storage, sources, scorer, notifiers,
                           dry_run=dry_run, progress=progress,
                           should_stop=should_stop)


def _run_search(config: Config, storage: Storage, sources: list[JobSource],
                scorer: Scorer, notifiers: list, *, dry_run: bool,
                progress: ProgressFn, should_stop: Callable[[], bool]) -> RunReport:
    report = RunReport()
    cancelled = False

    for source in sources:
        if cancelled:
            break
        if storage.in_cooldown(source.name):
            progress(f"[{source.name}] em cooldown — pulando")
            continue
        for filters in build_filters(config, source):
            if should_stop():
                cancelled = True
                progress("Busca cancelada.")
                break
            progress(f"[{source.name}] buscando: {filters.keywords}")
            found = new = 0
            error: str | None = None
            blocked = False
            try:
                for job in source.search(filters):
                    if should_stop():
                        cancelled = True
                        progress("Busca cancelada.")
                        break
                    found += 1
                    score, details = scorer.score(job)
                    if dry_run:
                        if not storage.exists(job.uid):
                            new += 1
                        report.preview.append((job, score))
                        continue
                    if storage.upsert_job(job, score, details):
                        new += 1
            except SourceBlockedError as exc:
                error = str(exc)
                blocked = True
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
                try:
                    storage.record_run(source.name, filters.keywords, found, new, error)
                except Exception:
                    log.exception("falha ao registrar run — seguindo")
            progress(f"[{source.name}] {filters.keywords}: {found} vagas, {new} novas")
            if blocked:
                break  # circuit breaker: não insiste nas próximas queries

    if notifiers and not dry_run:
        # Notifica tudo que está 'new' acima do threshold — inclui vagas de
        # execuções anteriores cuja notificação falhou (retry automático).
        rows = storage.list_jobs(status="new", min_score=config.scoring.threshold)
        jobs = [
            Job(source=row["source"], external_id=row["external_id"],
                title=row["title"], company=row["company"],
                location=row["location"], url=row["url"])
            for row in rows
        ]
        if not jobs:
            return report
        delivered = False
        for notifier in notifiers:
            try:
                notifier.notify(jobs)
                delivered = True
            except Exception as exc:
                log.exception("falha no notificador %s", type(notifier).__name__)
                report.errors.append(f"notify: {exc}")
        if delivered:
            storage.mark_notified([job.uid for job in jobs])
            report.notified = len(jobs)

    return report
