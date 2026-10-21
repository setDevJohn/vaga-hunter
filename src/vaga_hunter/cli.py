"""CLI: vaga-hunter run | serve | daemon | export."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from .config import Config, load_config
from .pipeline import build_sources, run_search
from .scoring import Scorer
from .storage import Storage


def _storage(config: Config) -> Storage:
    db = Path(config.db_path)
    if not db.is_absolute():
        db = config.base_dir / db
    return Storage(db)


def _notifiers(config: Config) -> list:
    from .notify import build_notifiers
    return build_notifiers(config)


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    storage = _storage(config)
    if args.query:
        config.search.queries = [args.query]
    sources = build_sources(config, storage)
    scorer = Scorer(config.profile, config.scoring)
    notifiers = [] if args.dry_run or args.no_notify else _notifiers(config)

    report = run_search(config, storage, sources, scorer, notifiers,
                        dry_run=args.dry_run, progress=print)

    print(f"\n{report.found} vagas encontradas, {report.new} novas, "
          f"{report.notified} notificadas.")
    if report.errors:
        print("Erros:", *report.errors, sep="\n  ")

    if args.dry_run:
        # dry-run não grava: mostra o que FOI encontrado agora, da memória
        print(f"\n{'SCORE':>5}  {'TÍTULO':<55}  {'EMPRESA':<25}  LOCAL")
        for job, score in sorted(report.preview, key=lambda p: p[1], reverse=True):
            print(f"{score:>5.1f}  {job.title[:55]:<55}  "
                  f"{job.company[:25]:<25}  {job.location[:30]}")
    else:
        rows = storage.list_jobs(min_score=args.min_score, limit=args.limit)
        if rows:
            print(f"\n{'SCORE':>5}  {'STATUS':<10}  {'TÍTULO':<55}  {'EMPRESA':<25}  LOCAL")
            for row in rows:
                print(f"{row['score']:>5.1f}  {row['status']:<10}  "
                      f"{row['title'][:55]:<55}  {row['company'][:25]:<25}  {row['location'][:30]}")
    storage.close()
    return 1 if report.errors else 0


def cmd_rescore(config: Config, args: argparse.Namespace) -> int:
    """Reaplica o scoring atual sobre as vagas já salvas (sem rede)."""
    from datetime import datetime

    from .models import Job
    from .scoring import Scorer

    storage = _storage(config)
    scorer = Scorer(config.profile, config.scoring)
    rows = storage.list_jobs(limit=100000)
    changed = above = 0
    for row in rows:
        posted = datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None
        job = Job(source=row["source"], external_id=row["external_id"],
                  title=row["title"], company=row["company"],
                  location=row["location"], url=row["url"],
                  description=row["description"] or "", posted_at=posted)
        score, details = scorer.score(job)
        if abs(score - row["score"]) >= 0.05:
            storage.update_score(row["uid"], score, details)
            changed += 1
        if score >= config.scoring.threshold:
            above += 1
    print(f"{len(rows)} vagas reavaliadas, {changed} scores alterados, "
          f"{above} acima do threshold ({config.scoring.threshold:.0f}).")
    storage.close()
    return 0


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    from .web.app import create_app
    app = create_app(config)
    print(f"vaga-hunter web em http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


def cmd_daemon(config: Config, args: argparse.Namespace) -> int:
    import signal
    import threading

    from .daemon import DaemonController

    storage = _storage(config)
    controller = DaemonController(config, storage)
    controller.start()
    print("Daemon iniciado (Ctrl+C para parar).")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    stop.wait()
    controller.stop()
    storage.close()
    print("Daemon encerrado.")
    return 0


def cmd_export(config: Config, args: argparse.Namespace) -> int:
    storage = _storage(config)
    rows = storage.list_jobs(limit=10000)
    records = [dict(row) for row in rows]
    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="")
    try:
        if args.format == "json":
            json.dump(records, out, ensure_ascii=False, indent=2)
        else:
            if records:
                writer = csv.DictWriter(out, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
    finally:
        if out is not sys.stdout:
            out.close()
            print(f"{len(records)} vagas exportadas para {args.output}")
    storage.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vaga-hunter",
                                     description="Bot de busca e ranqueamento de vagas")
    parser.add_argument("-c", "--config", help="caminho do config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="busca única")
    p_run.add_argument("--query", help="sobrescreve as queries do config")
    p_run.add_argument("--dry-run", action="store_true", help="não grava nem notifica")
    p_run.add_argument("--no-notify", action="store_true")
    p_run.add_argument("--min-score", type=float, default=None)
    p_run.add_argument("--limit", type=int, default=30)

    p_serve = sub.add_parser("serve", help="interface web")
    p_serve.add_argument("--port", type=int, default=5000)

    sub.add_parser("daemon", help="buscas periódicas no terminal")

    sub.add_parser("rescore", help="reaplica o scoring nas vagas salvas (sem rede)")

    p_export = sub.add_parser("export", help="exporta vagas")
    p_export.add_argument("--format", choices=["csv", "json"], default="csv")
    p_export.add_argument("--output", default="-")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)

    commands = {"run": cmd_run, "serve": cmd_serve, "daemon": cmd_daemon,
                "rescore": cmd_rescore, "export": cmd_export}
    return commands[args.command](config, args)


if __name__ == "__main__":
    raise SystemExit(main())
