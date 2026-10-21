"""Notificação desktop Linux via notify-send (falha silenciosa se indisponível)."""
from __future__ import annotations

import logging
import shutil
import subprocess

from ..models import Job
from .base import Notifier

log = logging.getLogger(__name__)

MAX_LISTED = 5


class DesktopNotifier(Notifier):
    def notify(self, jobs: list[Job]) -> None:
        if not shutil.which("notify-send"):
            log.debug("notify-send indisponível — pulando notificação desktop")
            return
        listed = "\n".join(f"• {job.title} — {job.company}" for job in jobs[:MAX_LISTED])
        if len(jobs) > MAX_LISTED:
            listed += f"\n… e mais {len(jobs) - MAX_LISTED}"
        try:
            subprocess.run(
                ["notify-send", "-a", "vaga-hunter", "-i", "system-search",
                 f"{len(jobs)} vaga(s) nova(s)", listed],
                check=False, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("notify-send falhou: %s", exc)
