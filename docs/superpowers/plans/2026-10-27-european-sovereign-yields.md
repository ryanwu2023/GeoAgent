# European Sovereign Yields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop daily Russian Duma election collection and add Germany, France, Euro Area 10-year yields plus the France-Germany spread to the Ukraine quantitative workspace.

**Architecture:** Keep `ukraine-aid-monitor` as the registered business collector while adding a focused `european_yields.py` sibling that publishes `output/geo-v6.json`; the existing adapter already imports this file alongside `ALL-records.jsonl`. TradingView daily history is primary, FRED monthly series are validation-only, and the derived spread uses same-day observations only.

**Tech Stack:** Python 3, `websocket-client`, FRED CSV, geo.v6 extension contract, FastAPI snapshot adapter, React/TypeScript/Vite.

**Spec:** `docs/superpowers/specs/2026-10-27-european-sovereign-yields-design.md`

## Global Constraints

- Preserve all historical Duma election records; only stop future election-specific polling and current-report emphasis.
- Publish only completed TradingView daily candles and never forward-fill FRED monthly values into daily observations.
- Missing, failed, and no-new-data states must remain distinct; never replace a missing yield with zero.
- Bind all four metrics to `topic=ukraine`, `dimension=aid`, `bucket=rates`, with `shared_market=false`.
- France-Germany spread equals `(FR10Y - DE10Y) * 100` on same-day valid observations and is expressed in basis points.
- Do not modify or delete the user's untracked spreadsheet files.

## Review Focus

- TradingView returns a partial symbol set: publish valid symbols, mark the collector partial, and derive no spread unless both country series overlap.
- TradingView returns an in-progress current-day candle: exclude it from published history.
- FRED is newer or older than the matching TradingView month: validation metadata must not overwrite or extend the daily series.
- Non-trading days have no new candle: report normal no-new-data rather than failure.
- Existing archived Duma election records still normalize and load after election sources and active report sections are disabled.

---

### Task 1: Retire Daily Duma Election Polling

**Files:**
- Modify: `collectors/russia-domestic-monitor/config.json`
- Modify: `collectors/russia-domestic-monitor/russia_domestic_monitor.py`
- Modify: `collectors/russia-domestic-monitor/README.md`
- Test: `tests/test_russia_domestic_retirement.py`

**Interfaces:**
- Consumes: existing source configuration and archived `ALL-records.jsonl` records.
- Produces: `active_bucket_order(cfg: dict) -> list[str]`, excluding `duma_election` from current runs while preserving historical classification support.

- [ ] **Step 1: Write failing tests for disabled election-only sources, current report exclusion, and historical classification compatibility.**
- [ ] **Step 2: Run `python -m pytest tests/test_russia_domestic_retirement.py -q` and verify the tests fail.**
- [ ] **Step 3: Disable only election-specific sources, route report ordering through `active_bucket_order`, and update stale title/boundary text without deleting the archived bucket definition.**
- [ ] **Step 4: Run the new test and `python collectors/russia-domestic-monitor/russia_domestic_monitor.py --selftest`; expect all checks to pass.**
- [ ] **Step 5: Commit the Duma retirement change.**

### Task 2: Add TradingView Daily Yield Collection and FRED Validation

**Files:**
- Create: `collectors/ukraine-aid-monitor/european_yields.py`
- Modify: `collectors/ukraine-aid-monitor/ukraine_aid_monitor.py`
- Modify: `collectors/ukraine-aid-monitor/run.bat`
- Modify: `collectors/ukraine-aid-monitor/run_new.bat`
- Modify: `collectors/ukraine-aid-monitor/README.md`
- Modify: `collectors/collectors.json`
- Modify: `requirements.txt`
- Test: `tests/test_european_yields_collector.py`

**Interfaces:**
- Produces: `fetch_tradingview_history(symbol: str, bars: int = 800) -> tuple[bytes, list[dict]]`.
- Produces: `fetch_fred_monthly(series_id: str) -> tuple[bytes, list[dict]]`.
- Produces: `same_day_spread(france: list[dict], germany: list[dict]) -> list[dict]`.
- Produces: `build_payload(output_root: Path, today: date | None = None) -> dict` using `schema_version='geo.v6/1'`.
- Produces: `main(argv: list[str] | None = None) -> int`, writing `geo-v6.json`, `source-status.json`, `_source_status.json`, and raw archives atomically.

- [ ] **Step 1: Write failing offline tests for three daily series, completed-day filtering, same-day spread calculation, partial-source behavior, FRED validation-only behavior, and zero/missing rejection.**
- [ ] **Step 2: Run `python -m pytest tests/test_european_yields_collector.py -q`; expect failures because the module does not exist.**
- [ ] **Step 3: Implement the collector with TradingView symbols `TVC:DE10Y`, `TVC:FR10Y`, `TVC:EU10Y` and FRED series `IRLTLT01DEM156N`, `IRLTLT01FRM156N`, `IRLTLT01EZM156N`; add `websocket-client` explicitly to runtime requirements.**
- [ ] **Step 4: Invoke the focused metric collector from both launchers after news collection; change the registry output kind to `records-and-metrics` while retaining the same collector ID.**
- [ ] **Step 5: Run the offline collector tests and the existing registry/selftest suites; expect all to pass.**
- [ ] **Step 6: Commit the yield collector and contract changes.**

### Task 3: Import, Prioritize, and Present the Ukraine Metrics

**Files:**
- Modify: `backend/workbench.py`
- Modify: `src/metric_category.ts`
- Test: `tests/test_european_yield_integration.py`
- Test: `tests/metric_category.test.cjs`

**Interfaces:**
- Consumes: four geo.v6 observations from Task 2.
- Produces: topic-scoped metric API rows searchable by German/French/Euro Area/yield/spread terms and eligible for Ukraine important-metric selection.

- [ ] **Step 1: Write failing backend integration tests that import a dual-output `ukraine-aid-monitor`, expose all four metrics only for `ukraine`, and preserve source/status metadata.**
- [ ] **Step 2: Add TypeScript assertions that all four metrics classify as `经济` from `bucket='rates'`.**
- [ ] **Step 3: Run the focused Python and Node tests; verify the new assertions fail before implementation.**
- [ ] **Step 4: Add the four series to Ukraine metric priorities and make any minimal importer/category adjustments required by the failing tests.**
- [ ] **Step 5: Run `python -m pytest tests/test_european_yield_integration.py tests/test_backend.py -q` and `node --test tests/metric_category.test.cjs`; expect PASS.**
- [ ] **Step 6: Commit the integration and presentation changes.**

### Task 4: Live Collection and End-to-End Verification

**Files:**
- Modify if required by verified defects: `collectors/ukraine-aid-monitor/european_yields.py`
- Modify if required by verified defects: `src/MetricChart.tsx`
- Test: `scripts/verify_latest_requests.py`

**Interfaces:**
- Consumes: production TradingView/FRED responses and the built snapshot.
- Produces: a refreshed snapshot where the Ukraine metric page renders four yield cards with values, dates, source status, axes, and hover detail.

- [ ] **Step 1: Run the collector once against live sources and inspect `geo-v6.json` for four non-duplicated observations, sorted unique dates, and valid source states.**
- [ ] **Step 2: Build a new snapshot and query the backend metric endpoint for `topic=ukraine`; verify all four series appear and do not appear for `topic=usiran`.**
- [ ] **Step 3: Run the Vite build plus `scripts/verify_latest_requests.py`; verify coordinates, hover values, current/previous values, and topic-specific display.**
- [ ] **Step 4: Run focused and regression suites: collector contracts, adapters/backend, metric category, and frontend build.**
- [ ] **Step 5: Review `git diff --check`, confirm the user spreadsheet remains untracked and untouched, then commit any live-compatibility fixes.**

