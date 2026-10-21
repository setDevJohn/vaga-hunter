"""Notificador Telegram via Bot API (sendMessage)."""
from __future__ import annotations

import html
import logging

import requests

from ..models import Job
from .base import Notifier

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
JOBS_PER_MESSAGE = 8


def format_message(jobs: list[Job]) -> str:
    lines = [f"🔎 <b>{len(jobs)} vaga(s) nova(s)</b>", ""]
    for job in jobs:
        title = html.escape(job.title)
        company = html.escape(job.company or "?")
        location = html.escape(job.location or "")
        lines.append(f'• <a href="{html.escape(job.url)}">{title}</a>')
        lines.append(f"   {company} — {location}")
    return "\n".join(lines)


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, timeout: float = 15.0):
        if not token or not chat_id:
            raise ValueError(
                "Telegram requer VAGA_HUNTER_TELEGRAM_TOKEN e VAGA_HUNTER_TELEGRAM_CHAT_ID")
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def notify(self, jobs: list[Job]) -> None:
        for i in range(0, len(jobs), JOBS_PER_MESSAGE):
            batch = jobs[i:i + JOBS_PER_MESSAGE]
            resp = requests.post(
                API_URL.format(token=self.token),
                json={
                    "chat_id": self.chat_id,
                    "text": format_message(batch),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout,
            )
            if not resp.ok:
                log.error("Telegram respondeu %d: %s", resp.status_code, resp.text[:200])
                resp.raise_for_status()
