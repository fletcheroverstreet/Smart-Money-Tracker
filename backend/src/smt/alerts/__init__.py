"""Outgoing alerts: the watchdog's findings, delivered through Apprise.

* `notifier` - where a message goes: Apprise (Discord, Telegram, email...) when
  APPRISE_URLS is set, otherwise just the log.
* `dispatch` - which findings still need a message, one combined message per
  watchdog run, and the bookkeeping that keeps each problem to one alert when
  it starts and one when it ends.
"""
