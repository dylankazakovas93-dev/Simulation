# Regression suite — Review 002 blockers

These tests are **red-by-design specifications** authored by the
architecture/model-risk lead. They encode the BLOCKER and HIGH findings from
`HANDOFF.md` → Review 002 as executable acceptance targets for Codex.

## How to run
They import `sim_core` and the canonical fixture, so run them on the branch that
contains the implementation (`codex/v1-core`), from the repo root:

```bash
python3 -m pytest tests/regression -q
```

On the governance branch (`claude/portfolio-sim-architecture-yq361s`) `sim_core`
is absent, so collection errors there — that is expected. The suite is meant to
travel with the implementation.

## Classification
- **RED** — fails against `fe408db` today; Codex must make it pass by fixing the
  implementation. Do **not** weaken the assertion to make it green.
- **GUARD** — passes today; pins a behavior that must not regress while the RED
  items are fixed.

Each test's docstring states its finding ID and the exact expected-current
failure. See `HANDOFF.md` → "Review 002 → Regression suite" for the index.

## Proposed contract for not-yet-implemented APIs
Some RED tests reference APIs that do not exist yet. Names are a **proposed**
contract — Codex may rename, but the asserted *behavior* must hold:

- `sim_core.execution.ensemble.run_path_ensemble(trades, policy, *, n_paths,
  master_seed, account=None, portfolio=None, coverage=None) -> ResultDistribution`
  — spawns one independent RNG stream per path from `SeedSequence(master_seed)`.
- `sim_core.models.Scenario` — serializable run config: `master_seed`,
  `resampling_policy`, `policy_params`, `account`, `portfolio`, `data_hash`;
  `.to_dict()` / `.from_dict()`.
- `sim_core.models.ResultDistribution` — `.scenario`, `.paths`, `.to_dict()` /
  `.from_dict()`.
- `sim_core.exports.export_simulation_batch(distribution, out_dir)` — writes a
  `run_manifest.json` carrying `master_seed`, `resampling_policy`,
  `policy_params`, `data_hash`, and a non-empty `limitations` list.
