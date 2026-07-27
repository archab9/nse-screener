"""Pipeline orchestration - one synchronous run per button press.

Assembles parameter results but does NOT apply toggles or tiers. Scoring is deliberately
separate so the GUI can re-score and re-rank instantly on every checkbox change without
re-reading any data.

Stage 1 is a manually-uploaded Chartink CSV. Stage 2 reads fundamentals either live from
Screener.in using the user's own login, or from a previously-saved local export.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from .config import resolve_path, settings
from .market.kite import KiteError, KiteSession, TokenState, check_token
from .models import RunContext, ScoredStock, Stage1Hit
from .stage1.csv_import import Stage1CsvError, load_chartink_csv
from .stage2.fundamentals import (
    CompanyFundamentals,
    FundamentalsError,
    FundamentalsStore,
    load_fundamentals,
    load_sector_index_valuations,
)
from .stage2.params import (
    evaluate_p1,
    evaluate_p2,
    evaluate_p3,
    evaluate_p4,
    evaluate_p5,
    evaluate_p6,
    evaluate_p7,
    evaluate_p8,
)
from .stage2.screener_client import (
    ScreenerAuthError,
    ScreenerClient,
    ScreenerParseError,
    has_credentials,
)

ProgressFn = Callable[[str, int], None]

LIVE = "live"
LOCAL = "local"


def _noop(_message: str, _pct: int) -> None:
    pass


class PipelineResult:
    def __init__(self, stocks: list[ScoredStock], context: RunContext) -> None:
        self.stocks = stocks
        self.context = context


def run_pipeline(
    chartink_csv: Path | str | None = None,
    data_source: str | None = None,
    progress: ProgressFn | None = None,
    today: date | None = None,
    use_kite: bool = True,
) -> PipelineResult:
    report = progress or _noop
    today = today or date.today()
    context = RunContext(run_at=today, as_of_trading_day=today)

    data_source = data_source or settings()["stage2"]["source"]

    # --- Kite token ------------------------------------------------------------
    report("Checking Kite access token...", 8)
    token = check_token()
    kite: KiteSession | None = None
    if token.ok and use_kite:
        kite = KiteSession()
    elif use_kite:
        severity = "warning" if token.state is TokenState.NO_API_KEY else "error"
        context.warn("kite_token", token.message, severity=severity)

    # --- stage 1 ---------------------------------------------------------------
    report("Loading Chartink CSV...", 18)
    csv_path = Path(chartink_csv) if chartink_csv else resolve_path("chartink_csv")
    try:
        hits = load_chartink_csv(csv_path)
    except Stage1CsvError as exc:
        context.warn("stage1_failed", str(exc), severity="error")
        return PipelineResult([], context)

    context.stage1_count = len(hits)
    if hits and hits[0].scan_date:
        context.as_of_trading_day = hits[0].scan_date
    if hits and all(h.price_backfilled for h in hits):
        context.warn(
            "stage1_no_prices",
            "The Chartink export carries symbols only - no close, %change or volume. "
            "Price fields are backfilled from Kite where available.",
            severity="info",
        )

    if not hits:
        context.warn(
            "stage1_empty",
            "Stage 1 returned no stocks. On a quiet day this is a normal result, not a failure.",
            severity="info",
        )
        return PipelineResult([], context)

    # --- stage 2 data ----------------------------------------------------------
    symbols = [h.symbol for h in hits]
    if data_source == LIVE:
        store, source_warnings = _load_live(symbols, report, context)
    else:
        store, source_warnings = _load_local(report, context, today)

    for warning in source_warnings:
        context.warn(*warning)
    if store is None:
        return PipelineResult([], context)

    sector_valuations = load_sector_index_valuations()
    if not sector_valuations:
        context.warn(
            "sector_index_missing",
            "No NSE sector-index PE/PB file found - P7's PB check will report as unavailable.",
            severity="warning",
        )

    # --- scoring ---------------------------------------------------------------
    report("Scoring fundamentals...", 80)
    stocks: list[ScoredStock] = []
    missing: list[str] = []

    for hit in hits:
        company = store.get(hit.symbol)
        if company is None:
            missing.append(hit.symbol)
            continue
        stocks.append(_evaluate(company, hit, store, sector_valuations))

    if missing:
        context.warn(
            "fundamentals_gap",
            f"{len(missing)} stage-1 stock(s) had no fundamentals and were not scored: "
            f"{', '.join(sorted(missing)[:12])}{' ...' if len(missing) > 12 else ''}",
            severity="warning",
        )

    # --- live price sanity check ----------------------------------------------
    if kite and stocks:
        report("Fetching live quotes...", 92)
        try:
            prices = kite.last_prices([s.symbol for s in stocks])
            for stock in stocks:
                stock.live_price = prices.get(stock.symbol)
                if stock.stage1 and stock.stage1.close is None:
                    stock.stage1.close = prices.get(stock.symbol)
        except KiteError as exc:
            context.warn("kite_quotes", f"Live quote check skipped: {exc}", severity="warning")

    report("Done.", 100)
    return PipelineResult(stocks, context)


def _load_live(
    symbols: list[str], report: ProgressFn, context: RunContext
) -> tuple[FundamentalsStore | None, list[tuple[str, str, str]]]:
    """Fetch fundamentals directly from Screener.in with the user's own login."""
    warnings: list[tuple[str, str, str]] = []

    if not has_credentials():
        return None, [
            (
                "screener_no_credentials",
                "No Screener.in credentials stored. Add them in the app, or switch the data "
                "source to a saved local export.",
                "error",
            )
        ]

    client = ScreenerClient()
    try:
        report("Logging in to Screener.in...", 25)
        client.login()
    except ScreenerAuthError as exc:
        return None, [("screener_auth", str(exc), "error")]

    premium = client.is_premium()
    if premium is False:
        warnings.append(
            (
                "screener_not_premium",
                "Signed in, but this account does not look like an active Premium "
                "subscription - some data may be limited.",
                "warning",
            )
        )

    def on_progress(symbol: str, i: int, total: int) -> None:
        report(f"Screener.in {i}/{total}: {symbol}", 25 + int(50 * i / max(total, 1)))

    try:
        store, failures = client.fetch_many(symbols, on_progress=on_progress)
    except ScreenerParseError as exc:
        return None, [("screener_parse", str(exc), "error")]

    if failures:
        sample = ", ".join(sorted(failures)[:8])
        warnings.append(
            (
                "screener_failures",
                f"{len(failures)} symbol(s) could not be read from Screener.in: {sample}"
                f"{' ...' if len(failures) > 8 else ''}",
                "warning",
            )
        )
    if not store.companies:
        warnings.append(
            (
                "screener_empty",
                "Screener.in returned no usable company data. The page layout may have "
                "changed - switch to a saved local export until the parser is updated.",
                "error",
            )
        )
        return None, warnings

    context.fundamentals_age_days = 0
    return store, warnings


def _load_local(
    report: ProgressFn, context: RunContext, today: date
) -> tuple[FundamentalsStore | None, list[tuple[str, str, str]]]:
    """Read a previously-saved export from disk."""
    warnings: list[tuple[str, str, str]] = []
    report("Loading saved fundamentals export...", 45)
    try:
        store = load_fundamentals()
    except FundamentalsError as exc:
        return None, [("fundamentals_missing", str(exc), "error")]

    age = store.age_days(today)
    context.fundamentals_age_days = age
    max_age = settings()["staleness"]["fundamentals_max_age_days"]
    if age is None:
        warnings.append(
            ("fundamentals_age_unknown", "Could not determine the export's age.", "warning")
        )
    elif age > max_age:
        warnings.append(
            (
                "fundamentals_stale",
                f"Saved export is {age} days old (over {max_age}). A newer quarter has "
                f"probably reported - refresh it, or switch the data source to live.",
                "warning",
            )
        )
    return store, warnings


def _evaluate(
    company: CompanyFundamentals,
    hit: Stage1Hit,
    store: FundamentalsStore,
    sector_valuations: dict[str, dict[str, float]],
) -> ScoredStock:
    peers = [
        c
        for c in store.companies.values()
        if c.industry and c.industry.strip().lower() == (company.industry or "").strip().lower()
    ]

    results = {
        "P1": evaluate_p1(company),
        "P2": evaluate_p2(company),
        "P3": evaluate_p3(company),
        "P4": evaluate_p4(company),
        "P5": evaluate_p5(company),
        "P6": evaluate_p6(company),
        "P7": evaluate_p7(company, sector_valuations),
        "P8": evaluate_p8(company, peers),
    }

    p7 = results["P7"]
    stock = ScoredStock(
        symbol=company.symbol,
        name=company.name or hit.name,
        industry=company.industry,
        results=results,
        stage1=hit,
        pegy=p7.evidence.get("PEGY") if isinstance(p7.evidence.get("PEGY"), (int, float)) else None,
        pb=company.pb,
    )
    stock.sector_tailwind = results["P8"].verdict.name == "YES"
    stock.tailwind_sector = str(results["P8"].evidence.get("Tailwind sector") or "")
    return stock
