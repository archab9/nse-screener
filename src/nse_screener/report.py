"""Rendering - ranked table and detail cards (spec section 6).

Plain-text here so the pipeline is testable and runnable headless; the GUI renders the
same structures as widgets.
"""

from __future__ import annotations

from .models import P8_ID, PARAM_IDS, RunContext, ScoredStock, Tier, Verdict
from .scoring import summary_line

DETAIL_CARD_MIN_TIER = Tier.QUALITY_GROWER


def format_banner(context: RunContext) -> str:
    if not context.warnings:
        return ""
    icons = {"error": "[!]", "warning": "[!]", "info": "[i]"}
    lines = [f"{icons.get(w.severity, '[-]')} {w.message}" for w in context.warnings]
    return "\n".join(lines)


def format_table(stocks: list[ScoredStock], toggles: dict[str, bool] | None = None) -> str:
    """Ticker | Sector | Core score | Tier | Sector tailwind | PEGY | PB | Key flags."""
    header = (
        f"{'Ticker':<12} {'Sector':<24} {'Score':>7} {'Tier':<17} "
        f"{'Tailwind':<9} {'PEGY':>6} {'PB':>6}  Key flags"
    )
    rows = [summary_line(toggles), "", header, "-" * len(header)]

    if not stocks:
        rows.append("(no stocks to rank)")
        return "\n".join(rows)

    for s in stocks:
        flags = "; ".join(
            f.message for f in s.all_flags if f.severity in ("risk", "positive")
        )
        rows.append(
            f"{s.symbol:<12} {(s.industry or '-')[:24]:<24} {s.score_display:>7} "
            f"{s.tier.value:<17} {'Yes' if s.sector_tailwind else 'No':<9} "
            f"{_num(s.pegy):>6} {_num(s.pb):>6}  {flags}"
        )
    return "\n".join(rows)


def format_detail_card(stock: ScoredStock, toggles: dict[str, bool] | None = None) -> str:
    from .scoring import normalise_toggles

    active = normalise_toggles(toggles)
    lines = [
        "=" * 78,
        f"{stock.symbol} - {stock.name}",
        f"{stock.industry or 'industry unknown'}",
        f"{stock.tier.value}  |  {stock.score_display} ({stock.pct_of_max:.0f}% of active max)"
        f"  |  Sector tailwind: {'Yes' if stock.sector_tailwind else 'No'}"
        + (f" ({stock.tailwind_sector})" if stock.tailwind_sector else ""),
        "=" * 78,
    ]

    for pid in PARAM_IDS:
        result = stock.results.get(pid)
        if result is None:
            continue
        state = "" if active.get(pid, True) else "  [OFF - excluded from score and denominator]"
        lines.append(f"\n{pid} {result.name}: {result.verdict.label}{state}")
        if result.detail:
            lines.append(f"    {result.detail}")
        for key, value in result.evidence.items():
            lines.append(f"      - {key}: {_evidence(value)}")

    p8 = stock.results.get(P8_ID)
    if p8:
        lines.append(f"\n{P8_ID} {p8.name}: {p8.verdict.label}  (reported separately, never scored)")
        if p8.detail:
            lines.append(f"    {p8.detail}")

    flags = stock.all_flags
    lines.append("\nFlags raised:")
    if flags:
        marker = {"risk": "[risk]", "positive": "[+]", "info": "[i]"}
        for flag in flags:
            lines.append(f"    {marker.get(flag.severity, '[-]')} {flag}")
    else:
        lines.append("    none")

    # Spec section 5 - manual research applies to finalists only.
    lines.append("\nManual research (this tier only, spec section 5):")
    lines.append("    1. FCF-turns-positive guidance from the last 1-2 concalls (P3)")
    lines.append("    2. Revenue-recognition / deferred-revenue risk in notes to accounts (P4)")
    lines.append("    3. Confirm sector-leadership and check for non-numeric disqualifiers (P8)")
    return "\n".join(lines)


def format_report(
    stocks: list[ScoredStock], context: RunContext, toggles: dict[str, bool] | None = None
) -> str:
    parts = []
    banner = format_banner(context)
    if banner:
        parts.append(banner)
    as_of = context.as_of_trading_day or context.run_at
    parts.append(f"Run {context.run_at:%d %b %Y} - data as of {as_of:%d %b %Y}")
    parts.append(f"Stage 1 returned {context.stage1_count} stock(s); {len(stocks)} scored.")
    parts.append("")
    parts.append(format_table(stocks, toggles))

    finalists = [s for s in stocks if s.tier.rank >= DETAIL_CARD_MIN_TIER.rank]
    if finalists:
        parts.append(f"\n\nDetail cards - {DETAIL_CARD_MIN_TIER.value} and above\n")
        parts.extend(format_detail_card(s, toggles) for s in finalists)
    return "\n".join(parts)


def _num(value: float | None, places: int = 2) -> str:
    return "-" if value is None else f"{value:.{places}f}"


def _evidence(value: object) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if value is None:
        return "n/a"
    return str(value)
