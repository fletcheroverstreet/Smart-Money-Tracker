"""Where alerts go.

Apprise speaks to about a hundred services through one URL format, for example
`discord://webhook_id/webhook_token` or `tgram://bot_token/chat_id`. The URLs
come from the APPRISE_URLS setting (comma separated). With no URLs set,
alerts are written to the log only, so a fresh install never fails for lack
of a chat account.

The URLs contain secrets (webhook and bot tokens), so this module never logs
them. It logs only the scheme, such as "discord".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import apprise

from smt.config import get_settings
from smt.logs import get_logger

log = get_logger("alerts")

#: Our alert levels mapped onto Apprise's message types (they set the icon/colour
#: in chat apps; the text itself always says what happened).
_APPRISE_TYPES = {
    "info": apprise.NotifyType.INFO,
    "success": apprise.NotifyType.SUCCESS,
    "warning": apprise.NotifyType.WARNING,
    "critical": apprise.NotifyType.FAILURE,
}


@dataclass(frozen=True)
class Alert:
    """One outgoing message."""

    title: str
    body: str
    #: info | success | warning | critical
    level: str = "info"


class Notifier(Protocol):
    """Anything that can deliver an Alert. Tests use a fake that records them."""

    #: What a delivered alert is recorded as: "sent" (really delivered) or "logged".
    delivered_state: str

    async def send(self, alert: Alert) -> bool:
        """Deliver the alert. True means it was accepted; False means try again later."""
        ...


class LogNotifier:
    """No APPRISE_URLS configured: alerts are written to the log and nowhere else."""

    delivered_state = "logged"

    async def send(self, alert: Alert) -> bool:
        log.warning("alert", level=alert.level, title=alert.title, body=alert.body)
        return True


class AppriseNotifier:
    """Deliver alerts to every configured Apprise URL."""

    delivered_state = "sent"

    def __init__(self, urls: list[str]) -> None:
        self._apprise = apprise.Apprise()
        #: How many of the URLs Apprise understood. Zero means nothing can be delivered.
        self.accepted = 0
        for url in urls:
            if self._apprise.add(url):
                self.accepted += 1
            else:
                # Never log the URL itself: it carries the token.
                log.error("alerts.url_rejected", scheme=url.split("://", 1)[0] or "?")

    async def send(self, alert: Alert) -> bool:
        if self.accepted == 0:
            return False
        result = await self._apprise.async_notify(
            body=alert.body, title=alert.title,
            notify_type=_APPRISE_TYPES.get(alert.level, apprise.NotifyType.INFO),
        )
        # Apprise returns True only when every target accepted the message.
        return bool(result)


def split_urls(value: str) -> list[str]:
    """APPRISE_URLS as a list: comma separated, blanks ignored."""
    return [part.strip() for part in value.split(",") if part.strip()]


def notifier_from_settings() -> Notifier:
    """Apprise when APPRISE_URLS is set, otherwise the log-only notifier."""
    urls = split_urls(get_settings().apprise_urls.get_secret_value())
    if not urls:
        return LogNotifier()
    return AppriseNotifier(urls)
