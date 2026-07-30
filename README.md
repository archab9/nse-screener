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
| Stage 1 — CSV upload, text file, or typed symbols | Working |
| Stage 2 — live Screener.in fetch (your Premium login) | Working; parser verified against live pages |
| Stage 2 — saved local export (fallback) | Working |
| Stage 2 — P1–P8 parameter rules | Working |
| Scoring engine — toggles, percentage tiering, no hard gate | Working, 154 tests |
| Editable thresholds per parameter | Working |
| Watchlist (add / remove / no action) | Working |
| Sector tailwind panel with leader highlighting | Working |
| Per-stock description (business USP + concall) | Working |
| PyQt6 desktop GUI | Working |
| Kite Connect — in-app API key entry, quotes, historical | Working; needs your API key |
| Backtest — pandas forward-return study | Working |

## The four tabs

**Screener** — pick an input, press Generate Results, see the ranked table and detail
cards. Each row has a watchlist control and a description column.

Rows shade green when the stock's Industry is *leadership-aligned (early signal)* **and**
the stock is top-3 by core score among stocks scored here in that Industry. A purple
ticker means the Sector is **Unresolved** — deliberately distinct from "evaluated and
didn't qualify", so a missing classification can never be mistaken for a considered miss.

The whole thing is optional: untick **Sector leadership overlay** and the Industry column,
the highlighting and the unresolved marking all disappear. It never affects any score or
tier either way.

**Thresholds** — every number the rules depend on, editable. Changing one re-evaluates
the current results immediately without re-fetching, since only the rules changed, not
the data. Fields differing from the shipped default are highlighted, and there's a reset.

**Watchlist** — sorted the same way History is, most parameters hit first, using each
stock's latest recorded run. Click any stock for the same full detail History shows.
**Run filter on entire watchlist** screens every watchlist stock through the normal
pipeline in one press. "Removed" is a distinct state from "never seen", so a stock you
dismissed stays dismissed instead of resurfacing as new.

## Peer rank and market cap

Every stock on every tab carries two extra columns:

**Cap** — Large / Mid / Small / Micro. SEBI classifies by *rank* (top 100 large, next 150
mid, rest small), not by an absolute figure, so when the bulk sector export is loaded the
true rank is used; otherwise the app falls back to configured rupee thresholds and will
disagree at the boundaries.

**Peer rank** — where the stock sits against its **subsector** and its **sector** on
**share price return over 1, 3 and 5 years**, with ROCE and growth as supporting context.
A stock can lead on one horizon and lag on another, so all three are ranked separately and
shown in the detail view. Rank 1 is the best performer in the group; companies not
reporting a metric leave that metric's denominator rather than counting as last.

This replaced a top-3-by-market-cap flag that was blank for nearly every stock and
returned nothing at all for smaller names Screener's industry table omits.

Ranking needs the bulk sector export to include **Return over 1year / 3years / 5years**.
Without those columns the Peer rank column reads `-` and everything else still works.

**Medals and trophies** — press **Run sector / subsector tests** on History or Watchlist.
Every sector and subsector is ranked by the **median six-month share price return** of its
constituents, and the top three of each level get 🥇 🥈 🥉. Median rather than mean, so one
multi-bagger cannot carry an otherwise flat sector; groups with fewer than five
constituents are excluded rather than allowed to win on a handful of names.

A stock earns 🏆 only when all four hold:

1. every active parameter passed outright (YES, not PARTIAL)
2. ranked #1 in its subsector on 1-year price return
3. its subsector holds a medal
4. its sector holds a medal

Deliberately strict — a trophy that appeared often would say nothing. The detail view
lists all four conditions with PASS/no against each, so it is always clear why a stock did
or didn't get one. Medals are recomputed from the current export every time, never frozen
into a saved run.

This needs **Return over 6months** in the bulk export alongside the 1/3/5-year columns.

The **Sector Leadership tab** is separate: it studies how widely fundamentals are
improving across an industry, which is a different question.

**History** — every run is recorded automatically and kept for 30 days. Stocks are listed
**most parameters hit first**, where a "hit" is a YES verdict on an active parameter.
That is deliberately not the same as the core score: four YES and three NO (8 points)
means more individual tests passed than seven PARTIAL (7 points). Click any stock for its
full parameter breakdown with the underlying numbers, every flag raised, and each earlier
run it appeared in. Use the dropdown to view one run or the whole window.

**Sector Leadership** — breadth of fundamental improvement per Industry, computed from a
bulk Screener.in sector export. Four toggleable conditions, an 8-company gate below which
an Industry shows "insufficient sample" rather than a meaningless percentage, quarterly
snapshots for trend, and a manual Auto / Force Yes / Force No per Industry.

**Sectors** — the five tailwind sectors, their rationale, how stale the review is, and
which of this run's stocks fall in each (leaders separated from plain members).

## Where state lives

Nothing user-specific is version-controlled. Threshold overrides, the watchlist,
classification overrides and breadth history all live under
`%LOCALAPPDATA%\nse-screener\`. Credentials are in Windows Credential Manager. The repo
holds shipped defaults only.

## Stage 1 input

Three ways in, all producing the same ticker list:

- **Chartink CSV export** — the file you download from Chartink.
- **Text file** — one symbol per line.
- **Type or paste** — commas, spaces and newlines all work.

Text and manual input carry no price data, so close/volume are backfilled from Kite where
available. Anything that doesn't look like an NSE symbol is reported back to you rather
than silently skipped.

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

### Desktop shortcut

```bash
powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
```

Puts an **NSE Screener** shortcut on the Desktop pointing at `pythonw.exe`, so the app
opens with no console window behind it. Regenerate the icon with
`python scripts\make_icon.py` if it goes missing.

Startup is logged to `%LOCALAPPDATA%\nse-screener\launch.log`. A double-clicked shortcut
has no console attached, so without that log a failure during startup would leave you with
a process that does nothing and no way to see why.

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
| Business description / USP | `About` and `Key Points` blocks, quoted verbatim |
| Concall transcripts | Documents section, restricted to `concall-link` rows |
| Concall summary | `/concalls/summary/` — Premium-gated; falls back to transcript links |
| Industry rank by market cap | the `/market/` industry table (public, whole industry) |

Two fields are **not** available and are handled as missing rather than guessed:
promoter **pledge %** (absent from the shareholding table, so P6's pledge flag never
fires on live data) and per-year ROE as published (derived instead, as above).

### On the stock descriptions

The description column and detail cards quote Screener.in — the About blurb, the Key
Points business-segment commentary, and Screener's own concall summary where your Premium
account can reach it. Nothing is generated or paraphrased here. If Screener has no
summary for a company, the card links the transcript instead of inventing a précis of an
earnings call next to a buy signal.

### On the sector list

The five tailwind sectors in `config/sector_map.json` are a **human 3-year macro
judgement**, not something the app derives. It cannot read the news and forecast sector
leadership, and pretending otherwise would make the output look better-founded than it
is. What the app does verify is membership in that list plus a real top-3-by-market-cap
rank from Screener.in's industry table — a stock is highlighted only when both hold. The
Sectors tab shows when the list was last reviewed and nags when it goes over 90 days.

### Credentials

Screener.in and Kite credentials live in Windows Credential Manager via `keyring`, or in
environment variables. Never in this repo.

- **Screener.in** — email and password entered in the app's own dialog, verified before
  saving, sent only to screener.in.
- **Kite Connect** — API key and secret entered under **Kite API...**, from
  developers.kite.trade. The daily access token comes from Zerodha's own browser login:
  the app opens the page, you log in there, and paste back the redirect URL. Your Zerodha
  password is never typed into this app.

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

An end-to-end sweep drives the assembled app the way a person does — loads data, presses
the button, toggles things, reads the table:

```bash
python scripts\verify_app.py
```

Add `--offline` to skip the checks that hit Screener.in.

## Not investment advice

This is a research tool that ranks stocks by a scoring rubric. It does not account for
your circumstances, and a high tier is not a recommendation to buy. Backtest results carry
survivorship and look-ahead bias that the free data tier cannot correct — the module
states both alongside every result.
