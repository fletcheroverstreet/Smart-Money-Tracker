# Sector labels

- `industry_map.csv` is copied from the owner's lodestar project on 2026-09-24.
  It's the hand-curated `ticker,industry,sector` map (lodestar DEC-009) for
  about 500 large US companies. It lives here too because a fresh server won't
  have lodestar. When lodestar's copy changes, copy it again.
- `sic_to_sector.yaml` is the fallback for everything else. It uses SEC SIC
  codes, which are broad and dated. Labels from it are stored with
  `sector_source = "sic"`.

Only the 11 broad sector names are shown publicly. The industry names in the
CSV follow commercial naming, so they're used internally, for example by the
committee→industry map.

No licensed GICS data is used.
