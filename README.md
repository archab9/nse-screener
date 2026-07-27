# nse-screener

A two-stage screener for stocks listed on India's National Stock Exchange (NSE).

**Stage 1** — you upload a Chartink momentum/volume scan export, which narrows the NSE
universe to breakout-day candidates. **Stage 2** — each survivor is scored against seven
weighted fundamental parameters pulled live from Screener.in using your Premium login,
then tiered and ranked. Sector tailwind is reported as a separate flag, never folded into
the score.

## Status

The engine runs end to end against stub fundamentals. What is built:

| Piece | State |
|---|---|
| Stage 1 — Chartink CSV upload | Working |
| Stage 2 — live Screener.in fetch (your Premium login) | Working; parser verified against live pages |
| Stage 2 — saved local export (fallback) | Working |
| Stage 2 — P1–P8 parameter rules | Working |
| Scoring engine — toggles, percentage tiering, no hard gate | Working, 113 tests |
| PyQt6 desktop GUI | Working |
| Kite Connect — token check, quotes, historical | Built; needs your API key to exercise |
| Backtest — pandas forward-return study | Working |

## Setup

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

Generate the stub fundamentals dataset used by `--source local` and the demo (invented
values, for exercising the engine without hitting Screener.in):

```bash
python scripts/make_sample_data.py
```

## Running

The desktop app is the intended interface — a single **Generate Results** button is the
only trigger. No scheduler, no background timer.

```bash
python run_app.py
```

Upload your Chartink scan export with the **Upload CSV...** button, pick the fundamentals
source, and press Generate Results.

A headless CLI is available for testing the pipeline:

```bash
python run_screener.py --source local --off P5 P7
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

**Stage 1 — Chartink, manual CSV upload.** You run the scan in Chartink and export the
CSV; the app reads that file. There is no scripted Chartink access and no scan clause in
this repo.

**Stage 2 — Screener.in, live via your own Premium login.** The app signs in with your
credentials and reads company pages directly. A saved local export remains selectable as a
fallback, and you want it: this is scraped markup, not an API, so selectors will eventually
break. When they do, switch the source to `local` and the screener keeps working.

**Kite Connect** (₹500/month) supplies live quotes and historical OHLCV. It carries no
fundamentals fields.

### What's parsed from a Screener.in company page

| Field | Source on the page |
|---|---|
| Market cap, P/E, book value, dividend yield | `#top-ratios` summary box |
| Quarterly sales and net profit | `#quarters` |
| Annual PAT | `#profit-loss` |
| CFO, free cash flow (capex derived) | `#cash-flow` |
| ROCE %, debtor days | `#ratios` |
| **ROE (derived)** | `#balance-sheet` — Screener publishes ROCE per year but not ROE, so it's computed as PAT ÷ (equity capital + reserves) |
| Promoter / FII / DII / government % | `#shareholding` |
| Blended EPS growth | mean of 3-year and 5-year compounded profit growth |
| Industry hierarchy | nested `/market/` links, broad → specific |

Two fields are **not** available and are handled as missing rather than guessed:
promoter **pledge %** (absent from the shareholding table, so P6's pledge flag never
fires on live data) and per-year ROE as published (derived instead, as above).

### Credentials

Screener.in and Kite credentials live in Windows Credential Manager via `keyring`, or in
environment variables. Never in this repo. The Screener.in password is entered in the
app's own dialog and sent only to screener.in.

## Refresh cadence

In live mode fundamentals are fetched fresh on every run, so there is nothing to refresh.
In local mode the app warns if the saved export is more than 100 days old — a newer
quarter has probably reported by then.

## Open items

1. **Sector-leader universe** — P8's "top 3 by market cap" is only as good as the peer set
   available. Ranking against the stage-1 shortlist alone flags itself provisional.
2. **NSE sector index PE/PB** — `data/nse/sector_index_valuation.csv` currently holds
   placeholder values. P7's PB check is only as good as that file.
3. **Scrape fragility** — the parser is verified against live pages today. Expect to
   revisit `screener_client.py` when Screener.in changes its markup.

## Testing

```bash
python -m pytest tests/ -q
```

## Not investment advice

This is a research tool that ranks stocks by a scoring rubric. It does not account for
your circumstances, and a high tier is not a recommendation to buy. Backtest results carry
survivorship and look-ahead bias that the free data tier cannot correct — the module
states both alongside every result.
