"""Daily prices: providers, the nightly loader, and read helpers (PLAN.md §6.10).

Free price data is licensed for personal use only, so under decision 1
(option B) prices appear in private mode only. Every stored bar carries its
provider and a `display_public` flag taken from the provider's licence.

    base.py     Bar, Licence and the PriceProvider protocol
    finlake.py  read-only reader for finlake's local parquet store
    yahoo.py    yfinance (private mode only)
    loader.py   idempotent upsert into prices_daily, re-base detection
    jobs.py     the nightly job prices.refresh_daily
    queries.py  reads for the API (private series; the public-only filter)
"""
