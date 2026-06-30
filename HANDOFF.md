# Handoff

## Work Completed

Implemented the Version 1 vertical slice:

- Project metadata via `pyproject.toml`.
- `sim_core` package with ingestion, resampling, execution, metrics, and export modules.
- Sample ES/NQ ledgers.
- Example JSON scenario configuration.
- README with quick start and schema summary.
- Architecture, decision, status, handoff, and limitation documents.
- Pytest suite for the initial core.

## Tests Run

```bash
python3 -m pytest
```

Result: 12 passed in 0.70s.

## Known Issues

See `KNOWN_LIMITATIONS.md`.

## Claude Review Focus

- Statistical validity of month-start timestamp shifting.
- Whether synchronized sampling should require all strategies to have explicit ledgers for sampled months.
- Whether replay should expose both entry-time and exit-time event streams now or later.
- Whether the Version 1 CSV schema is too permissive around optional prices and direction.

## Recommended Next Task

Add a small scenario runner that consumes `configs/v1_example.json`, generates multiple seeded paths, and writes `reports/path_summary.csv` and `reports/monthly_percentiles.csv`.
