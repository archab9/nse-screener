"""Shared formatting used by the Screener, History and Watchlist tabs.

One place so the three views cannot drift: a stock's pass/fail badges, its sector-leader
label and its headline positive read identically wherever they appear.
"""

from __future__ import annotations

from .models import PARAM_IDS, PARAM_NAMES

# YES / PARTIAL / NO / not evaluated.
BADGE = {"YES": "✓", "PARTIAL": "~", "NO": "✗", "N/A": "·"}
BADGE_LEGEND = "✓ passed   ~ partial   ✗ failed   · no data"

LEADER_RANKS = 3


def parameter_badges(verdicts: dict[str, str], active: dict[str, bool] | None = None) -> str:
    """Compact per-parameter pass/fail strip, e.g. 'P1✓ P2✓ P3~ P4✗ P5✓ P6✓ P7✗'.

    Parameters switched off show as [P5] so an absent verdict is never mistaken for a
    failure.
    """
    parts = []
    for pid in PARAM_IDS:
        if active is not None and not active.get(pid, True):
            parts.append(f"[{pid}]")
            continue
        parts.append(f"{pid}{BADGE.get(verdicts.get(pid, 'N/A'), '?')}")
    return " ".join(parts)


def passed_failed(verdicts: dict[str, str], active: dict[str, bool] | None = None) -> tuple[list[str], list[str]]:
    """(passed, failed) parameter names for the detail view.

    PARTIAL counts as neither: it is explicitly a half-credit verdict, and filing it under
    either heading would misstate what the rule actually found.
    """
    passed, failed = [], []
    for pid in PARAM_IDS:
        if active is not None and not active.get(pid, True):
            continue
        verdict = verdicts.get(pid)
        label = f"{pid} {PARAM_NAMES.get(pid, pid)}"
        if verdict == "YES":
            passed.append(label)
        elif verdict == "NO":
            failed.append(label)
    return passed, failed


def leader_label(rank: int | None, total: int | None, industry: str) -> str:
    """'#1 of 25 in Aerospace & Defense' when top-3, otherwise empty.

    Rank comes from Screener.in's own industry table - the whole industry ranked by market
    cap - so this is a verifiable fact rather than a judgement.
    """
    if not rank or rank > LEADER_RANKS or not industry:
        return ""
    of = f" of {total}" if total else ""
    return f"#{rank}{of} in {industry}"


def leader_short(rank: int | None, total: int | None, industry: str) -> str:
    """Column-width version: 'Top 3 - #1 Aerospace & Defense'."""
    label = leader_label(rank, total, industry)
    return f"Top {LEADER_RANKS} - {label}" if label else ""


def is_leader(rank: int | None) -> bool:
    return bool(rank and rank <= LEADER_RANKS)


def format_snapshot_detail(snap, appearances=None, retention_days: int = 30) -> str:
    """Full stock detail, shared by History and Watchlist so they cannot diverge.

    `snap` is a run_history.StockSnapshot; `appearances` an optional list of
    (RunRecord, StockSnapshot) for the same symbol.
    """
    active = snap.active_toggles
    passed, failed = passed_failed(snap.verdicts, active)

    lines = [
        "=" * 84,
        f"{snap.symbol} - {snap.name}",
        f"{snap.industry or 'industry unknown'}",
        f"{snap.tier}  |  {snap.score_display} ({snap.pct_of_max:.0f}% of active max)"
        f"  |  parameters hit: {snap.yes_count}",
    ]

    if snap.cap_category:
        cap = f"{snap.cap_category}"
        if snap.market_cap_cr:
            cap += f"  (Rs {snap.market_cap_cr:,.0f} cr)"
        lines.append(cap)

    if snap.peer_summary:
        where = " / ".join(x for x in (snap.peer_subsector, snap.peer_sector) if x)
        lines.append(f"PEER RANK: {snap.peer_summary}    [{where}]")

    if snap.pegy is not None or snap.pb is not None:
        bits = []
        if snap.pegy is not None:
            bits.append(f"PEGY {snap.pegy:.2f}")
        if snap.pb is not None:
            bits.append(f"PB {snap.pb:.2f}")
        lines.append("  |  ".join(bits))
    lines.append("=" * 84)

    lines.append(f"\nParameters   {parameter_badges(snap.verdicts, active)}")
    lines.append(f"             {BADGE_LEGEND}")
    lines.append(f"\nPassed ({len(passed)}):")
    lines.extend(f"    {name}" for name in passed) if passed else lines.append("    none")
    lines.append(f"\nFailed ({len(failed)}):")
    lines.extend(f"    {name}" for name in failed) if failed else lines.append("    none")

    if snap.headline_positive:
        lines.append("\nBiggest positive:")
        lines.append(f"    {snap.headline_positive}")

    if snap.concall_positives or snap.concall_negatives:
        lines.append(f"\nLatest concall{' ' + snap.concall_date if snap.concall_date else ''}:")
        for point in snap.concall_positives:
            lines.append(f"    + {point}")
        for point in snap.concall_negatives:
            lines.append(f"    - {point}")
        lines.append("    [positive/negative split is keyword-based - verify against the transcript]")
    elif snap.about:
        lines.append("\nBusiness:")
        lines.append(f"    {snap.about}")

    if snap.peer_ranks:
        lines.append(
            f"\nRank among peers  (1 = best; only companies reporting the metric are counted)"
        )
        lines.append(f"    subsector: {snap.peer_subsector or 'unknown'}"
                     f"    sector: {snap.peer_sector or 'unknown'}")
        lines.append(
            f"    {'Metric':<18} {'Value':>9}   {'In subsector':<16} {'In sector':<16}"
        )
        lines.append(f"    {'-' * 18} {'-' * 9}   {'-' * 16} {'-' * 16}")
        for label, data in snap.peer_ranks.items():
            value = data.get("value")
            sub = (f"#{data['sub_rank']} of {data['sub_total']}"
                   if data.get("sub_rank") else "-")
            sec = (f"#{data['sec_rank']} of {data['sec_total']}"
                   if data.get("sec_rank") else "-")
            lines.append(
                f"    {label:<18} {('-' if value is None else f'{value:.1f}'):>9}   "
                f"{sub:<16} {sec:<16}"
            )

    lines.append("\nParameter detail:")
    for pid in PARAM_IDS + ("P8",):
        param = snap.params.get(pid)
        if param is None:
            continue
        suffix = "  (reported separately, never scored)" if pid == "P8" else ""
        lines.append(f"\n  {pid} {PARAM_NAMES.get(pid, pid)}: {param.verdict}{suffix}")
        if param.detail:
            lines.append(f"      {param.detail}")
        for key, value in param.evidence.items():
            lines.append(f"        - {key}: {_evidence(value)}")

    lines.append("\nFlags raised:")
    lines.extend(f"    {flag}" for flag in snap.flags) if snap.flags else lines.append("    none")

    if appearances:
        lines.append(f"\nAppeared in {len(appearances)} run(s) in the last {retention_days} days:")
        for run, seen in appearances[:15]:
            lines.append(
                f"    {run.run_date:%d %b %Y}  {seen.score_display:>7}  "
                f"{seen.yes_count} hit  {seen.tier}"
            )
    return "\n".join(lines)


def _evidence(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k} {v}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return "n/a" if value is None else str(value)


def headline_positive(positives: list[str], key_points: list[str], about: str = "") -> str:
    """The single biggest positive to show alongside the stock.

    Preference order: the strongest positive concall takeaway, then the first Key Points
    line, then the About blurb. All three are Screener.in's words - the only thing chosen
    here is which one leads.
    """
    for candidate in (positives[0] if positives else "", *key_points, about):
        text = (candidate or "").strip()
        if len(text) > 12:
            return text[:200]
    return ""
