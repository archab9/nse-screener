"""Editable threshold schema for the GUI.

Describes which numbers in config/settings.json a user may change, what they mean, and
what range is sane. The GUI renders this; nothing else depends on it, so adding a new
editable threshold is a one-line change here.

`pair` marks two fields that form a lower/higher band (e.g. P2's 12-15% PARTIAL band).
The editor keeps low <= high so an inverted band can't be saved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import default_settings, settings


@dataclass
class ThresholdField:
    key: str          # config key inside the section
    label: str
    minimum: float
    maximum: float
    step: float = 0.5
    decimals: int = 1
    unit: str = "%"
    help: str = ""
    pair: str | None = None   # "low" or "high" when part of a band


@dataclass
class ThresholdGroup:
    param_id: str
    section: str      # top-level key in settings.json
    title: str
    fields: list[ThresholdField] = field(default_factory=list)


SCHEMA: list[ThresholdGroup] = [
    ThresholdGroup(
        "P1", "p1_record_financials", "Record financials",
        [
            ThresholdField("revenue_cagr_min_pct", "Revenue CAGR at least", 0, 100, 0.5, 1, "%",
                           "TTM-smoothed 5-year revenue CAGR needed for a YES."),
            ThresholdField("lookback_quarters", "Lookback window", 8, 40, 1, 0, "quarters",
                           "How many quarters the 'record high' is measured over."),
        ],
    ),
    ThresholdGroup(
        "P2", "p2_capital_efficiency", "Capital efficiency (ROCE / ROE)",
        [
            ThresholdField("roce_min_pct", "ROCE at least", 0, 60, 0.5, 1, "%"),
            ThresholdField("roce_min_pct_capital_intensive", "ROCE at least (capital-intensive)",
                           0, 60, 0.5, 1, "%",
                           "Relaxed bar for infra, utilities, capital goods, metals etc."),
            ThresholdField("roe_min_pct", "ROE at least", 0, 60, 0.5, 1, "%"),
            ThresholdField("years_required", "Years that must clear the bar", 1, 10, 1, 0, "of window"),
            ThresholdField("years_window", "Window length", 1, 10, 1, 0, "years"),
            ThresholdField("partial_band_low_pct", "PARTIAL band", 0, 60, 0.5, 1, "%",
                           "Returns inside this band score PARTIAL.", pair="low"),
            ThresholdField("partial_band_high_pct", "to", 0, 60, 0.5, 1, "%", pair="high"),
            ThresholdField("leverage_flag_roe_minus_roce_pp", "Flag leverage when ROE-ROCE exceeds",
                           0, 40, 0.5, 1, "pp",
                           "ROE can be inflated by debt; ROCE cannot."),
            ThresholdField("pricing_power_roce_pct", "Pricing-power flag above", 0, 100, 1, 0, "%",
                           "Informational only - never adds points."),
        ],
    ),
    ThresholdGroup(
        "P3", "p3_fcf_quality", "Free cash flow quality",
        [
            ThresholdField("fcf_yield_min_pct", "FCF yield at least", 0, 30, 0.25, 2, "%"),
            ThresholdField("fcf_yield_partial_low_pct", "PARTIAL from", 0, 30, 0.25, 2, "%",
                           pair="low"),
            ThresholdField("years_required_yes", "Positive-FCF years for YES", 1, 10, 1, 0, "years"),
            ThresholdField("years_required_partial", "Positive-FCF years for PARTIAL",
                           1, 10, 1, 0, "years"),
            ThresholdField("years_window", "Window length", 1, 10, 1, 0, "years"),
        ],
    ),
    ThresholdGroup(
        "P4", "p4_earnings_quality", "Earnings quality (CFO / PAT)",
        [
            ThresholdField("cfo_pat_min", "CFO/PAT at least", 0, 5, 0.05, 2, "x"),
            ThresholdField("partial_band_low", "PARTIAL from", 0, 5, 0.05, 2, "x", pair="low"),
            ThresholdField("years_required", "Years that must clear it", 1, 10, 1, 0, "of window"),
            ThresholdField("years_window", "Window length", 1, 10, 1, 0, "years"),
            ThresholdField("receivable_days_yoy_flag_pct", "Flag receivable days rising over",
                           0, 200, 5, 0, "% YoY"),
        ],
    ),
    ThresholdGroup(
        "P5", "p5_institutional", "Institutional conviction (FII / DII)",
        [
            ThresholdField("lookback_quarters", "Trend window", 2, 20, 1, 0, "quarters"),
            ThresholdField("flat_tolerance_pct", "Treat as flat within", 0, 10, 0.1, 2, "pp",
                           "A small decline inside this tolerance still counts as flat."),
            ThresholdField("marquee_exit_drop_pct", "Marquee-exit flag on a drop over",
                           0, 20, 0.25, 2, "pp"),
        ],
    ),
    ThresholdGroup(
        "P6", "p6_promoter", "Promoter stability",
        [
            ThresholdField("holding_strong_pct", "Strong holding above", 0, 100, 0.5, 1, "%"),
            ThresholdField("stability_tolerance_pct", "Or stable within", 0, 20, 0.25, 2, "pp"),
            ThresholdField("single_quarter_drop_pct", "Fail on a single-quarter drop of",
                           0, 50, 0.25, 2, "pp"),
            ThresholdField("lookback_quarters", "Window", 2, 20, 1, 0, "quarters"),
            ThresholdField("pledge_risk_pct", "Pledge risk flag above", 0, 100, 1, 0, "%",
                           "Not available from Screener.in live data - see README."),
        ],
    ),
    ThresholdGroup(
        "P7", "p7_valuation", "Valuation safety margin",
        [
            ThresholdField("pegy_max", "PEGY at most", 0, 10, 0.05, 2, "x"),
            ThresholdField("pegy_max_high_growth", "PEGY at most (high growth)",
                           0, 10, 0.05, 2, "x"),
            ThresholdField("high_growth_roce_pct", "High growth means ROCE above",
                           0, 100, 1, 0, "%"),
            ThresholdField("pb_sector_multiple_max", "PB at most, vs sector index PB",
                           0, 10, 0.05, 2, "x"),
        ],
    ),
    ThresholdGroup(
        "TIERS", "tiers", "Tier cutoffs (percentage of active maximum)",
        [
            ThresholdField("elite_compounder_min_pct", "ELITE COMPOUNDER from", 0, 100, 1, 0, "%"),
            ThresholdField("quality_grower_min_pct", "QUALITY GROWER from", 0, 100, 1, 0, "%"),
            ThresholdField("watchlist_min_pct", "WATCHLIST from", 0, 100, 1, 0, "%"),
        ],
    ),
]


def current_value(section: str, key: str) -> float:
    return float(settings()[section][key])


def default_value(section: str, key: str) -> float:
    return float(default_settings()[section][key])


def is_modified(section: str, key: str) -> bool:
    return current_value(section, key) != default_value(section, key)


def _integer_fields() -> set[tuple[str, str]]:
    """Fields declared with 0 decimals - counts of years or quarters, never fractional."""
    return {
        (group.section, f.key)
        for group in SCHEMA
        for f in group.fields
        if f.decimals == 0 and f.unit in ("years", "quarters", "of window")
    }


def build_overrides(values: dict[tuple[str, str], float]) -> dict[str, dict[str, float]]:
    """Turn {(section, key): value} into the nested shape settings.json uses.

    Values equal to the shipped default are omitted, so the override file stays a record
    of what the user actually changed rather than a full copy of the config.

    Count-like fields are written as int. A QDoubleSpinBox yields floats even at zero
    decimals, and a saved 4.0 crashed the list slicing in params.py.
    """
    integers = _integer_fields()
    out: dict[str, dict[str, float]] = {}
    for (section, key), value in values.items():
        if value == default_value(section, key):
            continue
        out.setdefault(section, {})[key] = int(value) if (section, key) in integers else value
    return out


def validate(values: dict[tuple[str, str], float]) -> list[str]:
    """Return human-readable problems. Empty list means the set is coherent."""
    problems: list[str] = []
    for group in SCHEMA:
        low = high = None
        low_label = high_label = ""
        for f in group.fields:
            if f.pair == "low":
                low, low_label = values.get((group.section, f.key)), f.label
            elif f.pair == "high":
                high, high_label = values.get((group.section, f.key)), f.label
        if low is not None and high is not None and low > high:
            problems.append(f"{group.title}: '{low_label}' ({low}) is above '{high_label}' ({high}).")

    tiers = {k: v for (s, k), v in values.items() if s == "tiers"}
    elite = tiers.get("elite_compounder_min_pct")
    quality = tiers.get("quality_grower_min_pct")
    watch = tiers.get("watchlist_min_pct")
    if None not in (elite, quality, watch) and not (elite >= quality >= watch):
        problems.append(
            "Tier cutoffs must descend: ELITE >= QUALITY GROWER >= WATCHLIST "
            f"(got {elite} / {quality} / {watch})."
        )
    return problems
