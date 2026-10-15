"""Application settings.

Everything configurable comes from environment variables, with a `.env` file
as a fallback (a real environment variable always wins). Tunables that change
how numbers are computed, such as signal thresholds, live in versioned YAML
under `config/` instead, so a change to them shows up in git history.

Read settings through `get_settings()`, never at import time in other
modules. finlake once bound its data path at import time, and a test run
deleted 15.5M real rows because of it. Reading lazily means the test
`conftest.py` can point everything at test resources before anything reads
them.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Repository root: backend/src/smt/config.py -> up three levels to the repo.
REPO_ROOT = Path(__file__).resolve().parents[3]
#: Versioned YAML configuration (funds, signals, overrides...).
CONFIG_DIR = REPO_ROOT / "config"
#: Per-user local data home. Same convention as finlake's ~/.finlake.
LOCAL_HOME = Path(os.environ.get("SMT_HOME", Path.home() / ".smart-money-tracker"))


class Settings(BaseSettings):
    """All runtime settings. Field names map to upper-case environment variables."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------ database
    database_url: str = "postgresql+psycopg://postgres@localhost:5433/smt"
    test_database_url: str = "postgresql+psycopg://postgres@localhost:5433/smt_test"

    # ------------------------------------------------------------ SEC EDGAR
    # The SEC requires a declared User-Agent: an app name plus a real contact
    # email. Undeclared or browser-like agents get HTTP 403 even at low rates.
    sec_user_agent: str = Field(default="", description="e.g. 'SmartMoneyTracker/0.1 you@example.com'")
    # Total requests/second across all SEC hosts from this IP. SEC ceiling: 10.
    sec_rate_limit: float = 8.0

    # ------------------------------------------------------------ storage
    raw_store_dir: Path = LOCAL_HOME / "raw"

    # ------------------------------------------------------------ prices (PLAN.md §6.10, decision 1)
    # finlake's data folder, read ONLY (FINLAKE_HOME, same variable finlake uses).
    # The default is built when settings are read, never at import time.
    finlake_home: Path = Field(default_factory=lambda: Path.home() / ".finlake")
    # Provider chain, in order of preference. yfinance runs only when private
    # mode is configured, because Yahoo's terms allow personal use only.
    price_providers: str = "finlake,yfinance"
    # How far back (calendar days) a security's first price load reaches.
    price_history_days: int = 730

    # ------------------------------------------------------------ secrets
    admin_token: SecretStr = SecretStr("")
    private_mode_passphrase: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")

    # ------------------------------------------------------------ optional keys
    openfigi_api_key: SecretStr = SecretStr("")
    finnhub_api_key: SecretStr = SecretStr("")
    api_data_gov_key: SecretStr = SecretStr("")

    # ------------------------------------------------------------ alerts
    # Where watchdog alerts go: Apprise URLs, comma separated (discord://...,
    # tgram://..., mailto://...). Empty means alerts are only written to the log.
    # APPRISE_URLS is the name to use; ALERT_URLS is still read for older .env files.
    # A secret, because these URLs embed webhook and bot tokens.
    apprise_urls: SecretStr = Field(default=SecretStr(""),
                                    validation_alias=AliasChoices("apprise_urls", "alert_urls"))
    # The watchdog reports an EDGAR feed as stale after this many minutes without
    # a successful poll during EDGAR hours (06:00-22:00 ET on SEC business days).
    watchdog_stale_minutes: int = 15
    # Company profiles (SEC submissions JSON) fetched per daily run: never-profiled
    # issuers first, then the most recently active. About 5,200 issuers had insider
    # filings in the last 180 days (2026-09-24), so the first fill takes ~2 days;
    # 2,500 requests take about 5 minutes on the lowest-priority SEC lane.
    submissions_batch_size: int = 2500

    # ------------------------------------------------------------ web
    public_origin: str = "http://localhost:3000"
    environment: str = "development"  # "development" | "test" | "production"

    @field_validator("sec_rate_limit")
    @classmethod
    def _rate_below_sec_ceiling(cls, value: float) -> float:
        """Refuse any configuration that could exceed the SEC's 10 req/s ceiling."""
        if not 0 < value < 10:
            raise ValueError("SEC_RATE_LIMIT must be between 0 and 10 (SEC ceiling is 10/s)")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def require_sec_user_agent(self) -> str:
        """Return the SEC User-Agent, or fail loudly if it isn't configured.

        A missing agent would get every request 403'd, which is easy to
        misread as "document not found". An explicit error names the cause.
        """
        agent = self.sec_user_agent.strip()
        if not agent or "@" not in agent:
            raise RuntimeError(
                "SEC_USER_AGENT is not set (or has no contact email). The SEC blocks "
                "undeclared clients. Set it to e.g. 'SmartMoneyTracker/0.1 you@example.com'."
            )
        return agent


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings (read once, on first use)."""
    return Settings()


def reset_settings_cache() -> None:
    """Forget cached settings. Tests call this after changing the environment."""
    get_settings.cache_clear()
