# Multi-firm rule-contract scope

This change introduces a typed, declarative contract registry.  It records
source provenance, supports only contracts whose required fields are sourced,
and maps compatible contracts to the existing generic lifecycle simulator.

It does not change resampling, deterministic seeds, trade ranking, result
schemas, prop-rule mathematics, or live web sourcing.  Rules that require
trade-level information unavailable in a point ledger are exposed as explicit
strict `UNKNOWN`, never as a passing assumption.

TPT Test is disabled because the supplied PDF does not contain a complete Test
evaluation table.  PRO+ is funded-only and does not create an automatic
transition from PRO.
