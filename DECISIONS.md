# Decisions

## 2026-06-30: Keep UI Out of Version 1

The initial deliverable is a tested simulation core. Streamlit is reserved in `app/` but not implemented until ingestion, resampling, replay, and metrics are reviewable.

## 2026-06-30: Use Typed Dataclasses for Initial Models

The brief allowed `pydantic` or equivalent validation. This repository currently has no dependency setup and the local environment does not have pydantic installed. Version 1 uses typed frozen dataclasses and explicit validation errors. A later scenario/config layer can move to pydantic once schemas stabilize.

## 2026-06-30: Normalize Ledgers by Entry Time, Realize Equity by Exit Time

Trade ledgers are sorted by `entry_time` to preserve chronological signal/trade ordering. Equity replay applies PnL at `exit_time`, because PnL is realized at close. This matters for overlapping positions and later exposure work.

## 2026-06-30: Month Bootstrap Shifts by Month-Start Offset

Sampled trades are shifted from source month start to target month start using timestamp offsets. This preserves intra-month spacing and intraday times, but month-end trades can overflow into the next calendar month when shifted into shorter months. This is documented as a Version 1 limitation.

## 2026-06-30: Synchronized Sampling Uses Source-Month Union

Source months are taken from the union of months present across all trades. When a strategy has no trades in a sampled source month, it contributes no trades for that target month. A stricter complete-panel mode may be added after review.
