"""Pipeline orchestration - one synchronous run per button press (spec section 10).

Assembles parameter results but does NOT apply toggles or tiers. Scoring is deliberately
separate so the GUI can re-score and re-rank instantly on every checkbox change without
re-reading any data.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import calendar_nse
from .config import resolve_path, settings
from .market.kite import KiteError, KiteSession, TokenState, check_token
from .models import PARAM_IDS, RunContext, ScoredStock, Stage1Hit
from .stage1.chartink import SCAN_CLAUSE, ChartinkError, run_scripted_scan
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

ProgressFn = Callable[[str, int], None]


def _noop(_message: str, _pct: int) -> None:
    pass


class PipelineResult:
    def __init__(self, stocks: list[ScoredStock], context: RunContext) -> None:
        self.stocks = stocks
        self.context = context


def run_pipeline(
    mode: str | None = None,
    progress: ProgressFn | None = None,
    today: date | None = None,
    use_kite: bool = True,
) -> PipelineResult:
    report = progress or _noop
    today = today or date.today()
    context = RunContext(run_at=today)

    # --- trading-day awareness -------------------------------------------------
    report("Checking NSE trading calendar...", 5)
    context.is_trading_day = calendar_nse.is_trading_day(today)
    context.as_of_trading_day = calendar_nse.last_trading_day(today)

    if not calendar_nse.calendar_is_verified(today.year):
        context.warn("calendar_unverified", calendar_nse.calendar_status(today.year))
    if not context.is_trading_day:
        context.warn(
            "not_a_trading_day",
            f"Showing {context.as_of_trading_day:%d %b %Y} - the last trading day. "
            f"{today:%d %b %Y} is not an NSE trading day.",
            severity="info",
        )

    # --- Kite token ------------------------------------------------------------
    report("Checking Kite access token...", 12)
    token = check_token()
    kite: KiteSession | None = None
    if token.ok and use_kite:
        kite = KiteSession()
    elif use_kite:
        severity = "warning" if token.state is TokenState.NO_API_KEY else "error"
        context.warn("kite_token", token.message, severity=severity)

    # --- stage 1 ---------------------------------------------------------------
    mode = mode or settings()["stage1"]["mode"]
    report(f"Running stage 1 ({mode} mode)...", 25)
    hits, stage1_warning = _load_stage1(mode)
    if stage1_warning:
        context.warn(*stage1_warning)
    context.stage1_count = len(hits)

    if not hits:
        # A quiet market legitimately produces zero names (spec sections 2 and 7).
        context.warn(
            "stage1_empty",
            "Stage 1 returned no stocks. On a quiet day this is a normal result, not a failure.",
            severity="info",
        )
        return PipelineResult([], context)

    # --- fundamentals ----------------------------------------------------------
    report("Loading Screener.in export...", 45)
    try:
        store = load_fundamentals()
    except FundamentalsError as exc:
        context.warn("fundamentals_missing", str(exc), severity="error")
        return PipelineResult([], context)

    age = store.age_days(today)
    context.fundamentals_age_days = age
    max_age = settings()["staleness"]["fundamentals_max_age_days"]
    if age is None:
        context.warn("fundamentals_age_unknown", "Could not determine the export's age.", "warning")
    elif age > max_age:
        context.warn(
            "fundamentals_stale",
            f"Screener.in export is {age} days old (over {max_age}). A newer quarter has "
            f"probably reported - re-export before trusting these scores.",
            severity="warning",
        )

    sector_valuations = load_sector_index_valuations()
    if not sector_valuations:
        context.warn(
            "sector_index_missing",
            "No NSE sector-index PE/PB file found - P7's PB check will report as unavailable.",
            severity="warning",
        )

    # --- stage 2 ---------------------------------------------------------------
    report("Scoring fundamentals...", 60)
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
            f"{len(missing)} stage-1 stock(s) absent from the export and not scored: "
            f"{', '.join(sorted(missing)[:12])}{' ...' if len(missing) > 12 else ''}",
            severity="warning",
        )

    # --- live price sanity check ----------------------------------------------
    if kite and stocks:
        report("Fetching live quotes...", 85)
        try:
            prices = kite.last_prices([s.symbol for s in stocks])
            for stock in stocks:
                stock.live_price = prices.get(stock.symbol)
                # Manual CSV mode carries no price columns - backfill from the quote.
                if stock.stage1 and stock.stage1.close is None:
                    stock.stage1.close = prices.get(stock.symbol)
        except KiteError as exc:
            context.warn("kite_quotes", f"Live quote check skipped: {exc}", severity="warning")

    report("Done.", 100)
    return PipelineResult(stocks, context)


def _load_stage1(mode: str) -> tuple[list[Stage1Hit], tuple[str, str, str] | None]:
    if mode == "scripted":
        try:
            return run_scripted_scan(SCAN_CLAUSE), None
        except ChartinkError as exc:
            # Fall back rather than fail the whole run - manual mode always works.
            try:
                hits = load_chartink_csv(resolve_path("chartink_csv"))
                return hits, (
                    "stage1_fallback",
                    f"Scripted Chartink scan failed ({exc}). Fell back to the manual CSV.",
                    "warning",
                )
            except Stage1CsvError as csv_exc:
                return [], ("stage1_failed", f"{exc}; CSV fallback also failed: {csv_exc}", "error")

    try:
        return load_chartink_csv(resolve_path("chartink_csv")), None
    except Stage1CsvError as exc:
        return [], ("stage1_failed", str(exc), "error")


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


def parameter_ids() -> tuple[str, ...]:
    return PARAM_IDS
