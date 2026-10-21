from unittest.mock import MagicMock, patch

import pytest

from vaga_hunter.config import Config
from vaga_hunter.models import Job
from vaga_hunter.notify import build_notifiers
from vaga_hunter.notify.desktop import DesktopNotifier
from vaga_hunter.notify.telegram import TelegramNotifier, format_message


def make_job(i: int) -> Job:
    return Job(source="x", external_id=str(i), title=f"Vaga <{i}>",
               company="Acme & Co", location="Remoto",
               url=f"https://example.com/{i}")


def test_format_message_escapes_html():
    msg = format_message([make_job(1)])
    assert "Vaga &lt;1&gt;" in msg
    assert "Acme &amp; Co" in msg
    assert '<a href="https://example.com/1">' in msg


def test_telegram_requires_credentials():
    with pytest.raises(ValueError):
        TelegramNotifier("", "")


@patch("vaga_hunter.notify.telegram.requests.post")
def test_telegram_batches_messages(mock_post):
    mock_post.return_value = MagicMock(ok=True)
    notifier = TelegramNotifier("tok", "chat")
    notifier.notify([make_job(i) for i in range(10)])  # 8 + 2
    assert mock_post.call_count == 2
    payload = mock_post.call_args_list[0].kwargs["json"]
    assert payload["chat_id"] == "chat"
    assert payload["parse_mode"] == "HTML"


@patch("vaga_hunter.notify.desktop.shutil.which", return_value=None)
def test_desktop_silent_without_notify_send(mock_which):
    DesktopNotifier().notify([make_job(1)])  # não deve lançar


@patch("vaga_hunter.notify.desktop.subprocess.run")
@patch("vaga_hunter.notify.desktop.shutil.which", return_value="/usr/bin/notify-send")
def test_desktop_calls_notify_send(mock_which, mock_run):
    DesktopNotifier().notify([make_job(i) for i in range(7)])
    args = mock_run.call_args.args[0]
    assert args[0] == "notify-send"
    assert "7 vaga(s) nova(s)" in args
    assert "e mais 2" in args[-1]


def test_build_notifiers_skips_telegram_without_token():
    config = Config()
    config.notify.telegram_token = ""
    notifiers = build_notifiers(config)
    assert len(notifiers) == 1
    assert isinstance(notifiers[0], DesktopNotifier)
