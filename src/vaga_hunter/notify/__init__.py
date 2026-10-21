"""Construção dos notificadores a partir do config."""
from __future__ import annotations

import logging

from ..config import Config
from .base import Notifier
from .desktop import DesktopNotifier
from .telegram import TelegramNotifier

log = logging.getLogger(__name__)


def build_notifiers(config: Config) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if config.notify.telegram:
        try:
            notifiers.append(TelegramNotifier(
                config.notify.telegram_token, config.notify.telegram_chat_id))
        except ValueError as exc:
            log.warning("Telegram desabilitado: %s", exc)
    if config.notify.desktop:
        notifiers.append(DesktopNotifier())
    return notifiers
