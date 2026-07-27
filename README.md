# nse-screener

A two-stage screener for stocks listed on India's National Stock Exchange (NSE).

**Stage 1** — a Chartink momentum/volume scan narrows the NSE universe to breakout-day
candidates. **Stage 2** — each survivor is scored against seven weighted fundamental
parameters built from a manually-exported Screener.in dataset, then tiered and ranked.
Sector tailwind is reported as a separate flag, never folded into the score.

## Status

The engine runs end to end against stub fundamentals. What is built:

| Piece | State |
|---|---|
| Stage 1 — manual Chartink CSV import | Working |
| Stage 1 — scripted POST to `chartink.com/screener/process` | Built; scan clause needs confirming (see below) |
| Stage 2 — P1–P8 parameter rules | Working |
| Scoring engine — toggles, percentage tiering, no hard gate | Working, 86 tests |
| PyQt6 desktop GUI | Working |
| Kite Connect — token check, quotes, historical | Built; needs your API key to exercise |
| Backtest — pandas forward-return study | Working |
| Real Screener.in export format | **Stubbed** — see "Open items" |

## Setup

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

Generate the stub fundamentals dataset (invented values, for testing the engine):

```bash
python scripts/make_sample_data.py
```

## Running

The desktop app is the intended interface — a single **Generate Results** button is the
only trigger. No scheduler, no background timer.

```bash
python -m nse_screener.gui.app
```

A headless CLI is available for testing the pipeline:

```bash
python run_screener.py --off P5 P7
```

## How scoring works

Each active parameter scores **YES = 2, PARTIAL = 1, NO = 0**. P6 (promoter stability) is
binary — YES or NO only.

Toggling a parameter off removes it from **both the score and the denominator**. It is not
zeroed out. This is why tiers are a percentage of the *active* maximum rather than a fixed
point total: 10 points is QUALITY GROWER out of 14, but ELITE COMPOUNDER out of 10.

| % of active max | Tier |
|---|---|
| ≥ 90% | ELITE COMPOUNDER |
| 70–89% | QUALITY GROWER |
| 50–69% | WATCHLIST |
| < 50% | EXCLUDED |

There is no AND-gate. A stock failing an active parameter still ranks — stage 1 is already
strict enough that gating would return an empty list on most days.

**P8 (sector tailwind) never contributes points.** The other seven parameters are audited
financial facts; P8 is a forward macro call. It is reported as a separate Yes/No column.

## Data sources

Chartink (free) and Screener.in Premium are the only data subscriptions. Kite Connect
(₹500/month) supplies live quotes and historical OHLCV — it has no fundamentals fields.

Two deliberate, permanent constraints:

- **Screener.in is never scripted.** Export CSVs manually from your own logged-in browser
  session; the code only ever reads files already on disk. Export is a feature built for a
  human clicking a button, and fundamentals only move quarterly anyway.
- **Chartink needs no login.** The scan clause is a filter definition, not private account
  data, so the scripted mode POSTs it with a CSRF token exactly as an anonymous visitor's
  browser does.

Kite is the only credential in the pipeline. It lives in Windows Credential Manager (via
`keyring`) or an environment variable — never in this repo.

## Refresh cadence

Fundamentals change when companies report, roughly every 90 days. Re-export from
Screener.in about four times a year, after each results season. Price, volume, and any
ratio built on today's price come from Kite and refresh on every run. The app warns if the
export on disk is more than 100 days old.

## Open items

These need your input before the screener is trustworthy on real money:

1. **NSE 2026 holiday list** — `config/nse_holidays_2026.json` is intentionally empty. The
   spec says 19 holidays but doesn't list them, and inventing an exchange calendar would
   silently mis-date results. Until filled, only weekends are detected and the app shows a
   warning banner.
2. **Chartink scan clause** — `SCAN_CLAUSE` in `src/nse_screener/stage1/chartink.py` is
   reconstructed from the seven conditions in the spec. Copy the real text from your saved
   scan's syntax view and paste it over.
3. **Screener.in export format** — the loader reads a documented four-CSV schema
   (`company`, `quarterly`, `annual`, `shareholding`). Send one real export and the adapter
   gets written against it.
4. **Sector-leader universe** — P8's "top 3 by market cap" is only as good as the peer set
   in the export. Scoring against a shortlist-only export flags the rank as provisional.

## Testing

```bash
python -m pytest tests/ -q
```

## Not investment advice

This is a research tool that ranks stocks by a scoring rubric. It does not account for
your circumstances, and a high tier is not a recommendation to buy. Backtest results carry
survivorship and look-ahead bias that the free data tier cannot correct — the module
states both alongside every result.
