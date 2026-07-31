"""The eight parameter rules from spec section 3.

Every threshold is read from config/settings.json. Where the spec left a value open, the
config entry carries a CONFIRMED-IN-CHAT note naming it as a decision rather than a
spec-stated number.

Each evaluator returns a ParamResult carrying the verdict, the numbers behind it, and any
flags raised. Nothing here knows about toggles or tiers - that is scoring.py's job.
"""

from __future__ import annotations

from ..config import sector_map, settings
from ..models import Flag, ParamResult, Verdict
from .fundamentals import CompanyFundamentals


def _count(cfg: dict, key: str) -> int:
    """Read a count-like setting as an int.

    Window lengths and 'N of M years' counts index and slice lists, so a float here is a
    TypeError. The GUI now writes ints, but config files get hand-edited too - a 5.0 in
    settings.json must not take the whole run down.
    """
    return int(cfg[key])


def _unknown(param_id: str, reason: str) -> ParamResult:
    return ParamResult(param_id=param_id, verdict=Verdict.UNKNOWN, detail=reason)


def _cagr(start: float, end: float, years: float) -> float | None:
    """Compound annual growth rate in percent. None when undefined (non-positive base)."""
    if start <= 0 or end <= 0 or years <= 0:
        return None
    return ((end / start) ** (1.0 / years) - 1.0) * 100.0


def _ttm_series(values: list[float | None]) -> list[float]:
    """Rolling 4-quarter sums, oldest first. Windows containing a gap are skipped."""
    out: list[float] = []
    for i in range(3, len(values)):
        window = values[i - 3 : i + 1]
        if any(v is None for v in window):
            continue
        out.append(sum(v for v in window if v is not None))
    return out


# --------------------------------------------------------------------------- P1

def evaluate_p1(company: CompanyFundamentals) -> ParamResult:
    """Record financials - TTM sales and profit at a 20-quarter high, plus revenue CAGR."""
    cfg = settings()["p1_record_financials"]
    quarters = company.sorted_quarterly()[-_count(cfg, "lookback_quarters") :]

    if len(quarters) < 8:
        return _unknown("P1", f"Need at least 8 quarters, have {len(quarters)}.")

    sales_ttm = _ttm_series([q.net_sales for q in quarters])
    profit_ttm = _ttm_series([q.net_profit for q in quarters])
    if not sales_ttm or not profit_ttm:
        return _unknown("P1", "Quarterly sales/profit series has gaps.")

    sales_at_high = sales_ttm[-1] >= max(sales_ttm)
    profit_at_high = profit_ttm[-1] >= max(profit_ttm)

    # Spec: CAGR from TTM-smoothed first-year vs last-year averages, not point-to-point.
    span_years = (len(quarters) - 1) / 4.0
    window = min(4, len(sales_ttm))
    first_avg = sum(sales_ttm[:window]) / window
    last_avg = sum(sales_ttm[-window:]) / window
    cagr = _cagr(first_avg, last_avg, max(span_years - 1.0, 1.0))

    evidence = {
        "TTM net sales (latest)": round(sales_ttm[-1], 1),
        "TTM net sales (5y max)": round(max(sales_ttm), 1),
        "TTM net profit (latest)": round(profit_ttm[-1], 1),
        "TTM net profit (5y max)": round(max(profit_ttm), 1),
        "Revenue CAGR %": round(cagr, 1) if cagr is not None else None,
        "Quarters used": len(quarters),
    }

    flags: list[Flag] = []
    cagr_ok = cagr is not None and cagr >= cfg["revenue_cagr_min_pct"]

    if sales_at_high and profit_at_high and cagr_ok:
        verdict, detail = Verdict.YES, (
            f"TTM sales and profit both at 5-year highs; revenue CAGR "
            f"{cagr:.1f}% >= {cfg['revenue_cagr_min_pct']}%."
        )
    elif sales_at_high and profit_at_high:
        verdict = Verdict.PARTIAL
        detail = (
            f"Both TTM metrics at 5-year highs but revenue CAGR "
            f"{cagr:.1f}%" if cagr is not None else "Both at highs but CAGR not computable"
        ) + f" is below {cfg['revenue_cagr_min_pct']}%."
        flags.append(
            Flag("P1", "cyclical_peak", "Cyclical peak - verify demand durability", "risk")
        )
    elif sales_at_high or profit_at_high:
        which = "sales" if sales_at_high else "profit"
        verdict = Verdict.PARTIAL
        detail = f"Only TTM {which} is at a 5-year high."
    else:
        verdict = Verdict.NO
        detail = "Neither TTM sales nor TTM profit is at a 5-year high."

    return ParamResult("P1", verdict, detail, evidence, flags)


# --------------------------------------------------------------------------- P2

def _is_capital_intensive(industry: str) -> bool:
    needle = (industry or "").lower()
    return any(term in needle for term in sector_map()["capital_intensive_industries"])


def _classification_of(company: CompanyFundamentals) -> str:
    """Prefer the full Screener.in hierarchy over the leaf label when both are present."""
    return company.classification_text


def evaluate_p2(company: CompanyFundamentals) -> ParamResult:
    """Capital efficiency - ROCE/ROE on 4-of-5 logic, with a leverage cross-check."""
    cfg = settings()["p2_capital_efficiency"]
    years = company.sorted_annual()[-_count(cfg, "years_window") :]
    roce = [y.roce_pct for y in years if y.roce_pct is not None]
    roe = [y.roe_pct for y in years if y.roe_pct is not None]

    if len(roce) < 3 or len(roe) < 3:
        return _unknown("P2", f"Need 3+ years of ROCE and ROE, have {len(roce)}/{len(roe)}.")

    capital_intensive = _is_capital_intensive(_classification_of(company))
    roce_bar = (
        cfg["roce_min_pct_capital_intensive"] if capital_intensive else cfg["roce_min_pct"]
    )
    roe_bar = cfg["roe_min_pct"]
    need = _count(cfg, "years_required")

    roce_hits = sum(1 for v in roce if v >= roce_bar)
    roe_hits = sum(1 for v in roe if v >= roe_bar)
    roce_ok = roce_hits >= need
    roe_ok = roe_hits >= need

    flags: list[Flag] = []
    leverage_years = [
        y.fy
        for y in years
        if y.roe_pct is not None
        and y.roce_pct is not None
        and (y.roe_pct - y.roce_pct) > cfg["leverage_flag_roe_minus_roce_pp"]
    ]
    if leverage_years:
        flags.append(
            Flag(
                "P2",
                "leverage_inflated_roe",
                f"ROE inflated by leverage - discount this signal (FY{', FY'.join(str(y) for y in leverage_years)})",
                "risk",
            )
        )
    if any(v > cfg["pricing_power_roce_pct"] for v in roce):
        flags.append(Flag("P2", "pricing_power", "Pricing power (ROCE >25%)", "positive"))

    evidence = {
        "ROCE by FY %": [round(v, 1) for v in roce],
        "ROE by FY %": [round(v, 1) for v in roe],
        "ROCE bar %": roce_bar,
        "Capital-intensive exception": capital_intensive,
        f"Years ROCE >= {roce_bar}%": f"{roce_hits}/{len(roce)}",
        f"Years ROE >= {roe_bar}%": f"{roe_hits}/{len(roe)}",
    }

    band_low, band_high = cfg["partial_band_low_pct"], cfg["partial_band_high_pct"]
    latest_roce, latest_roe = roce[-1], roe[-1]
    in_band = band_low <= latest_roce < band_high and band_low <= latest_roe < band_high
    improving = len(roce) >= 2 and roce[-1] > roce[0] and len(roe) >= 2 and roe[-1] > roe[0]

    if roce_ok and roe_ok and not leverage_years:
        verdict = Verdict.YES
        detail = f"ROCE {roce_hits}/{len(roce)} and ROE {roe_hits}/{len(roe)} years clear the bar, no leverage flag."
    elif roce_ok and roe_ok and leverage_years:
        # Both bars cleared but ROE is leverage-driven: spec says discount, not full credit.
        verdict = Verdict.PARTIAL
        detail = "Both thresholds met but ROE-ROCE gap indicates leverage-driven returns."
    elif roce_ok or roe_ok:
        which = "ROCE" if roce_ok else "ROE"
        verdict = Verdict.PARTIAL
        detail = f"Only {which} clears the 4-of-5 threshold."
    elif in_band or improving:
        verdict = Verdict.PARTIAL
        detail = f"Returns in the {band_low}-{band_high}% band or clearly improving."
    else:
        verdict = Verdict.NO
        detail = f"ROCE/ROE below {band_low}% or declining."

    return ParamResult("P2", verdict, detail, evidence, flags)


# --------------------------------------------------------------------------- P3

def evaluate_p3(company: CompanyFundamentals) -> ParamResult:
    """Free cash flow quality - FCF positive 4-of-5 years and FCF yield >= 2%."""
    cfg = settings()["p3_fcf_quality"]
    years = company.sorted_annual()[-_count(cfg, "years_window") :]
    fcf = [(y.fy, y.fcf) for y in years if y.fcf is not None]

    if len(fcf) < 3:
        return _unknown("P3", f"Need 3+ years of CFO and capex, have {len(fcf)}.")

    positive = sum(1 for _, v in fcf if v > 0)
    latest_fcf = fcf[-1][1]

    fcf_yield = None
    if company.market_cap_cr and company.market_cap_cr > 0 and latest_fcf is not None:
        fcf_yield = latest_fcf / company.market_cap_cr * 100.0

    evidence = {
        "FCF by FY": {f"FY{fy}": round(v, 1) for fy, v in fcf},
        "Years FCF positive": f"{positive}/{len(fcf)}",
        "FCF yield %": round(fcf_yield, 2) if fcf_yield is not None else None,
        "Market cap (cr)": company.market_cap_cr,
    }

    yield_ok = fcf_yield is not None and fcf_yield >= cfg["fcf_yield_min_pct"]
    yield_partial = (
        fcf_yield is not None
        and cfg["fcf_yield_partial_low_pct"] <= fcf_yield < cfg["fcf_yield_min_pct"]
    )

    if positive >= _count(cfg, "years_required_yes") and yield_ok:
        verdict = Verdict.YES
        detail = f"FCF positive {positive}/{len(fcf)} years, FCF yield {fcf_yield:.1f}%."
    elif positive >= _count(cfg, "years_required_partial") or yield_partial:
        verdict = Verdict.PARTIAL
        detail = f"FCF positive {positive}/{len(fcf)} years" + (
            f", yield {fcf_yield:.1f}%." if fcf_yield is not None else ", yield not computable."
        )
    elif positive == 0:
        verdict = Verdict.NO
        detail = "FCF consistently negative."
    else:
        verdict = Verdict.NO
        detail = f"FCF positive only {positive}/{len(fcf)} years."

    return ParamResult("P3", verdict, detail, evidence, [])


# --------------------------------------------------------------------------- P4

def evaluate_p4(company: CompanyFundamentals) -> ParamResult:
    """Earnings quality - CFO/PAT >= 0.8 in 2 of last 3 FY (spec resolves the 3-vs-2 contradiction)."""
    cfg = settings()["p4_earnings_quality"]
    years = company.sorted_annual()[-_count(cfg, "years_window") :]
    ratios = [(y.fy, y.cfo_pat) for y in years if y.cfo_pat is not None]

    if len(ratios) < 2:
        return _unknown("P4", f"Need 2+ years of CFO and PAT, have {len(ratios)}.")

    hits = sum(1 for _, r in ratios if r >= cfg["cfo_pat_min"])
    latest = ratios[-1][1]

    flags: list[Flag] = []
    rec = [(y.fy, y.receivable_days) for y in years if y.receivable_days is not None]
    if len(rec) >= 2 and rec[-2][1] and rec[-2][1] > 0:
        change = (rec[-1][1] - rec[-2][1]) / rec[-2][1] * 100.0
        if change > cfg["receivable_days_yoy_flag_pct"]:
            flags.append(
                Flag(
                    "P4",
                    "receivable_days_spike",
                    f"Receivable days up {change:.0f}% YoY (>{cfg['receivable_days_yoy_flag_pct']:.0f}%)",
                    "risk",
                )
            )

    evidence = {
        "CFO/PAT by FY": {f"FY{fy}": round(r, 2) for fy, r in ratios},
        f"Years CFO/PAT >= {cfg['cfo_pat_min']}": f"{hits}/{len(ratios)}",
        "Receivable days": {f"FY{fy}": v for fy, v in rec} or None,
    }

    declining = len(ratios) >= 3 and ratios[-1][1] < ratios[-2][1] < ratios[-3][1]

    if hits >= _count(cfg, "years_required"):
        verdict = Verdict.YES
        detail = f"CFO/PAT >= {cfg['cfo_pat_min']} in {hits} of {len(ratios)} years."
    elif latest >= cfg["partial_band_low"] and not declining:
        verdict = Verdict.PARTIAL
        detail = f"Latest CFO/PAT {latest:.2f} sits in the {cfg['partial_band_low']}-{cfg['cfo_pat_min']} band."
    else:
        verdict = Verdict.NO
        detail = (
            f"CFO/PAT {latest:.2f} below {cfg['partial_band_low']}"
            if latest < cfg["partial_band_low"]
            else "CFO/PAT on a clear declining trend."
        )

    return ParamResult("P4", verdict, detail, evidence, flags)


# --------------------------------------------------------------------------- P5

def evaluate_p5(company: CompanyFundamentals) -> ParamResult:
    """Institutional conviction from Screener.in's FII/DII aggregates only.

    Spec section 3 P5 names the tradeoff explicitly: no mutual-fund split out of DII is
    available from Screener.in, so this is a two-signal read, not a three-signal one.
    """
    cfg = settings()["p5_institutional"]
    points = company.sorted_shareholding()[-_count(cfg, "lookback_quarters") :]
    fii = [(p.quarter, p.fii_pct) for p in points if p.fii_pct is not None]
    dii = [(p.quarter, p.dii_pct) for p in points if p.dii_pct is not None]

    if len(fii) < 3 or len(dii) < 3:
        return _unknown("P5", f"Need 3+ quarters of FII and DII, have {len(fii)}/{len(dii)}.")

    tol = cfg["flat_tolerance_pct"]
    fii_delta = fii[-1][1] - fii[0][1]
    dii_delta = dii[-1][1] - dii[0][1]
    fii_ok = fii_delta >= -tol
    dii_ok = dii_delta >= -tol

    flags: list[Flag] = []
    for label, series in (("FII", fii), ("DII", dii)):
        for i in range(1, len(series)):
            drop = series[i - 1][1] - series[i][1]
            if drop > cfg["marquee_exit_drop_pct"]:
                flags.append(
                    Flag(
                        "P5",
                        "marquee_exit",
                        f"Marquee exit - {label} fell {drop:.1f}pp in {series[i][0]}",
                        "risk",
                    )
                )

    evidence = {
        "FII % trend": [f"{q}: {v:.2f}" for q, v in fii],
        "DII % trend": [f"{q}: {v:.2f}" for q, v in dii],
        "FII change (pp)": round(fii_delta, 2),
        "DII change (pp)": round(dii_delta, 2),
        "Note": "FII/DII aggregates only - no MF split available from Screener.in",
    }

    if fii_ok and dii_ok:
        verdict, detail = Verdict.YES, "Both FII and DII rising or flat over the window."
    elif fii_ok or dii_ok:
        rising = "FII" if fii_ok else "DII"
        falling = "DII" if fii_ok else "FII"
        verdict = Verdict.PARTIAL
        detail = f"{rising} rising/flat, {falling} declining."
    else:
        verdict, detail = Verdict.NO, "Both FII and DII declining."

    return ParamResult("P5", verdict, detail, evidence, flags)


# --------------------------------------------------------------------------- P6

def evaluate_p6(company: CompanyFundamentals) -> ParamResult:
    """Promoter stability. Binary YES/NO per spec - no partial credit."""
    cfg = settings()["p6_promoter"]
    points = company.sorted_shareholding()[-_count(cfg, "lookback_quarters") :]
    holdings = [(p.quarter, p.promoter_pct) for p in points if p.promoter_pct is not None]

    if len(holdings) < 2:
        return _unknown("P6", f"Need 2+ quarters of promoter holding, have {len(holdings)}.")

    values = [v for _, v in holdings]
    latest = values[-1]
    spread = max(values) - min(values)
    worst_drop = max(
        (values[i - 1] - values[i] for i in range(1, len(values))), default=0.0
    )

    flags: list[Flag] = []
    pledge = next(
        (p.pledge_pct for p in reversed(points) if p.pledge_pct is not None), None
    )
    if pledge is not None and pledge > cfg["pledge_risk_pct"]:
        flags.append(
            Flag("P6", "pledge_risk", f"Promoter pledge {pledge:.1f}% (>{cfg['pledge_risk_pct']:.0f}%)", "risk")
        )

    evidence = {
        "Promoter % trend": [f"{q}: {v:.2f}" for q, v in holdings],
        "Range (pp)": round(spread, 2),
        "Largest single-quarter drop (pp)": round(worst_drop, 2),
        "Pledge %": pledge,
        "PSU/government-held": company.is_psu,
    }

    strong = latest > cfg["holding_strong_pct"]
    stable = spread <= cfg["stability_tolerance_pct"]
    no_cliff = worst_drop < cfg["single_quarter_drop_pct"]

    # PSU/bank exception: government holding above the strong bar qualifies on its own.
    if company.is_psu and strong:
        return ParamResult(
            "P6", Verdict.YES, f"PSU exception - government holding {latest:.1f}%.", evidence, flags
        )

    if (strong or stable) and no_cliff:
        verdict = Verdict.YES
        basis = f"holding {latest:.1f}% > {cfg['holding_strong_pct']:.0f}%" if strong else f"stable within {spread:.2f}pp"
        detail = f"Promoter {basis}, no single-quarter drop >= {cfg['single_quarter_drop_pct']:.0f}pp."
    elif not no_cliff:
        verdict = Verdict.NO
        detail = f"Single-quarter promoter drop of {worst_drop:.2f}pp."
    else:
        verdict = Verdict.NO
        detail = f"Promoter holding {latest:.1f}% neither above {cfg['holding_strong_pct']:.0f}% nor stable within {cfg['stability_tolerance_pct']:.0f}pp."

    return ParamResult("P6", verdict, detail, evidence, flags)


# --------------------------------------------------------------------------- P7

def _sector_index_for(industry: str) -> str:
    smap = sector_map()
    needle = (industry or "").lower()
    for term, index_name in smap["nse_sector_index"].items():
        if term in needle:
            return index_name
    return smap["nse_sector_index_default"]


def evaluate_p7(
    company: CompanyFundamentals, sector_valuations: dict[str, dict[str, float]] | None = None
) -> ParamResult:
    """Valuation safety margin - PEGY plus a PB check against the NSE sector index.

    PEGY threshold is absolute (<=1.0), CONFIRMED-IN-CHAT: NSE publishes no sector EPS
    growth, so no genuine sector PEGY benchmark is computable from free data.
    """
    cfg = settings()["p7_valuation"]
    sector_valuations = sector_valuations or {}

    if company.pe is None or company.pe <= 0:
        return _unknown("P7", "PE unavailable or non-positive.")
    if company.eps_cagr_pct is None:
        return _unknown("P7", "EPS growth unavailable - PEGY not computable.")

    div_yield = company.dividend_yield_pct or 0.0
    denominator = company.eps_cagr_pct + div_yield
    if denominator <= 0:
        return ParamResult(
            "P7",
            Verdict.NO,
            f"EPS growth + dividend yield is {denominator:.1f}% - PEGY undefined, treated as no margin of safety.",
            {"PE": company.pe, "EPS CAGR %": company.eps_cagr_pct, "Dividend yield %": div_yield},
        )

    pegy = company.pe / denominator
    index_name = _sector_index_for(_classification_of(company))
    sector_pb = (sector_valuations.get(index_name) or {}).get("pb") or None

    latest_roce = next(
        (y.roce_pct for y in reversed(company.sorted_annual()) if y.roce_pct is not None), None
    )
    high_growth = latest_roce is not None and latest_roce > cfg["high_growth_roce_pct"]
    pegy_bar = cfg["pegy_max_high_growth"] if high_growth else cfg["pegy_max"]
    pegy_ok = pegy <= pegy_bar

    pb_ok = None
    pb_limit = None
    if sector_pb and company.pb is not None:
        pb_limit = sector_pb * cfg["pb_sector_multiple_max"]
        pb_ok = company.pb <= pb_limit

    evidence = {
        "PE": company.pe,
        "EPS CAGR %": company.eps_cagr_pct,
        "Dividend yield %": div_yield,
        "PEGY": round(pegy, 2),
        "PEGY bar": pegy_bar,
        "High-growth exception (ROCE >20%)": high_growth,
        "PB": company.pb,
        "Sector index": index_name,
        "Sector index PB": sector_pb,
        "PB limit": round(pb_limit, 2) if pb_limit else None,
    }

    if pb_ok is None:
        # Half the test is unavailable; report on PEGY alone and say so rather than
        # inventing a sector benchmark.
        verdict = Verdict.PARTIAL if pegy_ok else Verdict.NO
        detail = f"PEGY {pegy:.2f} vs bar {pegy_bar}; sector PB benchmark for {index_name} unavailable."
    elif pegy_ok and pb_ok:
        verdict = Verdict.YES
        detail = f"PEGY {pegy:.2f} <= {pegy_bar} and PB {company.pb:.2f} within {cfg['pb_sector_multiple_max']}x sector PB ({sector_pb:.2f})."
    elif pegy_ok or pb_ok:
        verdict = Verdict.PARTIAL
        met = "PEGY" if pegy_ok else "PB"
        detail = f"Only {met} criterion met (PEGY {pegy:.2f}, PB {company.pb:.2f} vs limit {pb_limit:.2f})."
    else:
        verdict = Verdict.NO
        detail = f"Both stretched - PEGY {pegy:.2f} > {pegy_bar}, PB {company.pb:.2f} > {pb_limit:.2f}."

    return ParamResult("P7", verdict, detail, evidence, [])


# --------------------------------------------------------------------------- P8

def evaluate_p8(company: CompanyFundamentals, reference=None) -> ParamResult:
    """Where this stock stands among its sector and subsector peers on ONE-YEAR share
    price return. Reported separately, never scored (spec section 4).

    Replaces the old tailwind / top-3 flag, which said nothing about the great majority of
    stocks. This gives the actual position: "#17 of 42" is an answer, "not top 3" was not.

    Peers come from the bulk sector reference export, the only dataset here covering the
    whole market. Companies not reporting a one-year return leave the denominator rather
    than counting as last.
    """
    from ..peer_ranking import rank_symbol

    ranking = rank_symbol(reference, company.symbol)
    metric = ranking.metric("return_1y")

    if metric is None or not (metric.subsector.known or metric.sector.known):
        return _unknown(
            "P8", "Not found in the sector reference export, so no peer rank is possible."
        )

    sub, sec = metric.subsector, metric.sector
    value = sub.value if sub.value is not None else sec.value
    evidence = {
        "1-year price return %": round(value, 1) if value is not None else None,
        "Subsector": ranking.subsector or "unknown",
        "Rank in subsector": sub.display,
        "Sector": ranking.sector or "unknown",
        "Rank in sector": sec.display,
    }

    cfg = settings().get("p8_peer_rank", {})
    percentile = sub.percentile if sub.percentile is not None else sec.percentile
    if percentile is None:
        verdict = Verdict.PARTIAL
    elif percentile <= cfg.get("top_band_pct", 25.0):
        verdict = Verdict.YES
    elif percentile <= cfg.get("mid_band_pct", 50.0):
        verdict = Verdict.PARTIAL
    else:
        verdict = Verdict.NO

    where = " and ".join(
        part for part in (
            f"#{sub.rank} of {sub.total} in {ranking.subsector}" if sub.known else "",
            f"#{sec.rank} of {sec.total} in {ranking.sector}" if sec.known else "",
        ) if part
    )
    return ParamResult("P8", verdict, f"1-year price return ranks {where}.", evidence, [])


EVALUATORS = {
    "P1": evaluate_p1,
    "P2": evaluate_p2,
    "P3": evaluate_p3,
    "P4": evaluate_p4,
    "P5": evaluate_p5,
    "P6": evaluate_p6,
}
