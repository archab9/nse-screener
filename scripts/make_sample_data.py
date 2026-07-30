"""Generate the stub fundamentals dataset (spec section 0 / section 8).

Hand-shaped profiles rather than random numbers, so each parameter's YES / PARTIAL / NO /
N-A branch is exercised by at least one company and the scoring engine can be verified
before the real Screener.in export format is wired in.

    python scripts/make_sample_data.py

Every symbol here is drawn from the user's real Chartink 'VOLUME SCAN.csv', but ALL
FINANCIAL VALUES ARE INVENTED for testing. This file is a fixture, not market data.
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fundamentals"
NSE_OUT = ROOT / "data" / "nse"

QUARTERS = [f"{y}-Q{q}" for y in range(2021, 2026) for q in range(1, 5)]
FY_YEARS = [2021, 2022, 2023, 2024, 2025]
SH_QUARTERS = QUARTERS[-6:]


def growth(base: float, rate: float, n: int) -> list[float]:
    return [round(base * ((1 + rate) ** i), 1) for i in range(n)]


def seasonal(series: list[float]) -> list[float]:
    """Add a repeating Q3 bump so a naive 'highest quarter' check would misfire."""
    return [round(v * (1.18 if i % 4 == 2 else 1.0), 1) for i, v in enumerate(series)]


PROFILES = {
    # Strong across the board - should land ELITE COMPOUNDER.
    "NEWGEN": {
        "industry_rank": 1, "industry_peer_count": 18,
        "name": "Newgen Software Technologies Ltd",
        "industry": "Software - Application",
        "market_cap_cr": 14500,
        "pe": 32.0, "pb": 7.4, "eps_cagr_pct": 34.0, "dividend_yield_pct": 0.4,
        "sales": seasonal(growth(210, 0.055, 20)),
        "profit": seasonal(growth(34, 0.062, 20)),
        "roce": [22.1, 24.0, 26.4, 28.9, 30.2],
        "roe": [17.8, 19.4, 21.0, 22.6, 24.1],
        "cfo": [180, 215, 260, 310, 372], "capex": [45, 52, 60, 70, 82],
        "pat": [165, 198, 240, 288, 345],
        "receivable_days": [96, 94, 92, 90, 89],
        "promoter": [61.2, 61.2, 61.0, 60.9, 60.9, 60.8],
        "pledge": [0, 0, 0, 0, 0, 0],
        "fii": [8.1, 8.9, 9.6, 10.4, 11.2, 12.0],
        "dii": [12.4, 13.0, 13.5, 14.1, 14.8, 15.3],
    },
    # Quality but expensive, and ROE is leverage-driven - PARTIAL on P2 and P7.
    "KALYANKJIL": {
        "industry_rank": 2, "industry_peer_count": 24,
        "name": "Kalyan Jewellers India Ltd",
        "industry": "Retail - Jewellery",
        "market_cap_cr": 52000,
        "pe": 78.0, "pb": 12.1, "eps_cagr_pct": 28.0, "dividend_yield_pct": 0.2,
        "sales": seasonal(growth(3100, 0.048, 20)),
        "profit": seasonal(growth(92, 0.070, 20)),
        "roce": [13.2, 14.8, 16.1, 17.4, 18.0],
        "roe": [21.0, 23.4, 25.1, 26.8, 27.5],   # ROE-ROCE gap > 8pp -> leverage flag
        "cfo": [420, 300, 510, 640, 720], "capex": [280, 340, 420, 500, 610],
        "pat": [430, 380, 560, 690, 780],
        "receivable_days": [22, 24, 26, 27, 35],  # >20% YoY jump -> flag
        "promoter": [60.6, 60.6, 60.5, 60.5, 60.4, 60.4],
        "pledge": [0, 0, 0, 0, 0, 0],
        "fii": [12.1, 12.8, 13.4, 13.0, 12.6, 12.2],
        "dii": [9.4, 9.9, 10.6, 11.2, 11.8, 12.4],
    },
    # Cyclical peak: both TTM metrics at highs but CAGR below 12%.
    "VSSL": {
        "industry_rank": 11, "industry_peer_count": 40,
        "name": "Vardhman Special Steels Limited",
        "industry": "Steel & Iron Products",
        "market_cap_cr": 3400,
        "pe": 24.0, "pb": 2.6, "eps_cagr_pct": 9.0, "dividend_yield_pct": 0.6,
        "sales": seasonal(growth(380, 0.021, 20)),
        "profit": seasonal(growth(18, 0.024, 20)),
        "roce": [11.2, 12.4, 13.1, 12.8, 13.4],   # capital-intensive -> 10% bar
        "roe": [9.8, 10.6, 11.4, 10.9, 11.6],
        "cfo": [92, 68, 110, 96, 128], "capex": [70, 88, 74, 102, 96],
        "pat": [88, 74, 104, 92, 118],
        "receivable_days": [58, 60, 61, 63, 64],
        "promoter": [58.4, 58.4, 58.3, 58.3, 58.2, 58.2],
        "pledge": [0, 0, 0, 0, 0, 0],
        "fii": [2.1, 2.0, 1.8, 1.7, 1.5, 1.4],
        "dii": [4.2, 4.0, 3.9, 3.7, 3.5, 3.3],   # both declining -> P5 NO
    },
    # Promoter instability + pledge + marquee exit - should fail P6, flag heavily.
    "BLKASHYAP": {
        "name": "B. L. Kashyap And Sons Limited",
        "industry": "Construction & Infrastructure",
        "market_cap_cr": 1350,
        "pe": 41.0, "pb": 3.2, "eps_cagr_pct": 6.0, "dividend_yield_pct": 0.0,
        "sales": seasonal(growth(300, 0.012, 20)),
        "profit": seasonal(growth(9, -0.005, 20)),
        "roce": [7.8, 8.4, 9.1, 8.6, 9.4],
        "roe": [5.2, 6.0, 6.8, 6.1, 7.0],
        "cfo": [40, -22, 35, -18, 44], "capex": [55, 48, 62, 51, 68],
        "pat": [52, 30, 58, 34, 62],
        "receivable_days": [120, 128, 134, 141, 149],
        "promoter": [58.1, 57.9, 52.4, 52.2, 52.0, 51.8],  # 5.5pp cliff -> P6 NO
        "pledge": [24.0, 24.0, 26.5, 26.5, 28.0, 28.0],    # pledge risk flag
        "fii": [4.8, 4.6, 2.1, 1.9, 1.7, 1.5],             # >2pp drop -> marquee exit
        "dii": [3.1, 3.0, 2.9, 2.8, 2.7, 2.6],
    },
    # Defense tailwind, rich valuation - P8 Yes, P7 poor. Spec predicts exactly this.
    "AJAXENGG": {
        "industry_rank": 3, "industry_peer_count": 31,
        "name": "Ajax Engineering Ltd",
        "industry": "Industrial Machinery & Capital Goods",
        "market_cap_cr": 8200,
        "pe": 46.0, "pb": 8.9, "eps_cagr_pct": 21.0, "dividend_yield_pct": 0.5,
        "sales": seasonal(growth(240, 0.040, 20)),
        "profit": seasonal(growth(30, 0.048, 20)),
        "roce": [24.5, 26.1, 27.8, 26.9, 28.4],
        "roe": [19.2, 20.4, 21.8, 21.1, 22.3],
        "cfo": [150, 178, 205, 232, 268], "capex": [40, 48, 55, 62, 71],
        "pat": [140, 165, 192, 218, 250],
        "receivable_days": [72, 71, 70, 69, 68],
        "promoter": [66.2, 66.2, 66.1, 66.1, 66.0, 66.0],
        "pledge": [0, 0, 0, 0, 0, 0],
        "fii": [5.2, 5.6, 6.1, 6.5, 7.0, 7.4],
        "dii": [8.8, 9.2, 9.5, 9.9, 10.3, 10.8],
    },
    # Deliberately sparse - exercises the UNKNOWN / N-A branches.
    "PICCADIL": {
        "name": "Piccadily Agro Industries Ltd",
        "industry": "Breweries & Distilleries",
        "market_cap_cr": 2100,
        "pe": None, "pb": 6.2, "eps_cagr_pct": None, "dividend_yield_pct": 0.0,
        "sales": seasonal(growth(120, 0.05, 8)),   # only 8 quarters
        "profit": seasonal(growth(14, 0.06, 8)),
        "roce": [18.2, 20.4], "roe": [16.8, 18.9],  # only 2 years
        "cfo": [60, 74], "capex": [22, 28], "pat": [58, 70],
        "receivable_days": [44, 46],
        "promoter": [61.0, 61.0], "pledge": [0, 0],
        "fii": [1.2, 1.4], "dii": [2.0, 2.2],
    },
}


def write(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  {path.relative_to(ROOT)}  ({len(rows)} rows)")


def _write_sector_reference() -> None:
    """A stand-in for the bulk Screener.in sector export.

    Headers deliberately use Screener's on-screen column wording rather than the loader's
    internal field names, so the alias matching in sector_reference.py is exercised.
    Industry sizes are chosen to straddle the 8-company gate: Aerospace & Defense has 10
    (computed), Petroleum Products has 4 (suppressed as insufficient sample).
    """
    industries = {
        # (sector, industry, n_companies, n_passing)
        ("Industrials", "Aerospace & Defense"): (10, 7),      # 70% -> leadership-aligned
        ("Financial Services", "Private Sector Bank"): (12, 6),  # 50% -> watch
        ("Materials", "Cement & Cement Products"): (9, 2),    # 22% -> not flagged
        ("Energy", "Petroleum Products"): (4, 3),             # below the gate
    }

    rows = []
    counter = 0
    for (sector, industry), (total, passing) in industries.items():
        for i in range(total):
            counter += 1
            good = i < passing
            # Two companies per industry clear everything EXCEPT ROCE. Without them every
            # company would pass or fail all four conditions at once, and toggling a
            # condition off would never change a single number - which would make the
            # toggle look correct even if it were broken.
            borderline = passing <= i < passing + 2
            rows.append([
                f"SYM{counter:03d}", f"{industry} Co {i + 1}", sector, industry,
                f"{industry} - sub",
                18.0 if (good or borderline) else 4.0,    # sales growth 3Y
                22.0 if (good or borderline) else 3.0,    # profit growth 3Y
                19.0 if (good or borderline) else 11.0,   # OPM
                15.0 if (good or borderline) else 12.0,   # OPM preceding year
                20.0 if good else 8.0,                    # ROCE - the discriminator
                0.4, 12000 - counter * 50,
                # Share price return 1y / 3y / 5y, spread so ranks differ per horizon.
                round((62.0 if good else 4.0) - i * 3.1, 1),
                round((140.0 if good else 12.0) - i * 5.5, 1),
                round((210.0 if good else 20.0) - i * 8.0, 1),
            ])

    # Two real symbols so the classification path can be exercised end to end.
    rows.append(["HAL", "Hindustan Aeronautics", "Industrials", "Aerospace & Defense",
                 "Aerospace & Defense", 20.0, 25.0, 21.0, 18.0, 32.0, 0.0, 305175,
                 71.4, 305.0, 620.0])
    rows.append(["BEL", "Bharat Electronics", "Industrials", "Aerospace & Defense",
                 "Aerospace & Defense", 17.0, 21.0, 26.0, 24.0, 38.0, 0.0, 288005,
                 58.2, 268.0, 540.0])

    # The stub-fundamentals companies, so the local demo resolves against this file.
    # PICCADIL is left out ON PURPOSE - it demonstrates the Unresolved state, which must
    # look different from "evaluated and did not qualify".
    demo = [
        ("NEWGEN", "Industrials", "Aerospace & Defense", True),
        ("AJAXENGG", "Industrials", "Aerospace & Defense", True),
        ("KALYANKJIL", "Financial Services", "Private Sector Bank", False),
        ("VSSL", "Materials", "Cement & Cement Products", False),
        ("BLKASHYAP", "Materials", "Cement & Cement Products", False),
    ]
    for symbol, sector, industry, good in demo:
        rows.append([
            symbol, symbol.title(), sector, industry, f"{industry} - sub",
            18.0 if good else 4.0, 22.0 if good else 3.0,
            19.0 if good else 11.0, 15.0 if good else 12.0,
            20.0 if good else 8.0, 0.4, 9000,
            68.0 if good else 3.0, 155.0 if good else 9.0, 240.0 if good else 15.0,
        ])

    write(
        ROOT / "data" / "sector_reference" / "screener_sector_export.csv",
        ["NSE Code", "Name", "Sector", "Industry", "Basic Industry",
         "Sales growth 3Years", "Profit growth 3Years", "OPM", "OPM last year",
         "ROCE", "Debt to equity", "Market Capitalization",
         "Return over 1year", "Return over 3years", "Return over 5years"],
        rows,
    )


def main() -> None:
    print("Writing stub fundamentals (INVENTED VALUES - fixture only):")

    write(
        OUT / "company.csv",
        ["symbol", "name", "industry", "market_cap_cr", "pe", "pb",
         "eps_cagr_pct", "dividend_yield_pct", "is_psu",
         "industry_rank", "industry_peer_count"],
        [
            [sym, p["name"], p["industry"], p["market_cap_cr"], p["pe"], p["pb"],
             p["eps_cagr_pct"], p["dividend_yield_pct"], "false",
             p.get("industry_rank", ""), p.get("industry_peer_count", "")]
            for sym, p in PROFILES.items()
        ],
    )

    write(
        OUT / "quarterly.csv",
        ["symbol", "quarter", "net_sales", "net_profit"],
        [
            [sym, QUARTERS[-len(p["sales"]) :][i], p["sales"][i], p["profit"][i]]
            for sym, p in PROFILES.items()
            for i in range(len(p["sales"]))
        ],
    )

    write(
        OUT / "annual.csv",
        ["symbol", "fy", "roce_pct", "roe_pct", "cfo", "capex", "pat", "receivable_days"],
        [
            [sym, FY_YEARS[-len(p["roce"]) :][i], p["roce"][i], p["roe"][i],
             p["cfo"][i], p["capex"][i], p["pat"][i], p["receivable_days"][i]]
            for sym, p in PROFILES.items()
            for i in range(len(p["roce"]))
        ],
    )

    write(
        OUT / "shareholding.csv",
        ["symbol", "quarter", "promoter_pct", "pledge_pct", "fii_pct", "dii_pct", "public_pct"],
        [
            [sym, SH_QUARTERS[-len(p["promoter"]) :][i], p["promoter"][i], p["pledge"][i],
             p["fii"][i], p["dii"][i],
             round(100 - p["promoter"][i] - p["fii"][i] - p["dii"][i], 2)]
            for sym, p in PROFILES.items()
            for i in range(len(p["promoter"]))
        ],
    )

    _write_sector_reference()

    # NSE sectoral index PE/PB - real indices, PLACEHOLDER values pending a live pull.
    write(
        NSE_OUT / "sector_index_valuation.csv",
        ["index_name", "pe", "pb", "dividend_yield"],
        [
            ["NIFTY BANK", 14.2, 2.4, 0.9],
            ["NIFTY FINANCIAL SERVICES", 18.6, 3.1, 0.8],
            ["NIFTY AUTO", 24.1, 4.6, 1.1],
            ["NIFTY PHARMA", 32.4, 5.2, 0.7],
            ["NIFTY FMCG", 41.8, 10.4, 1.9],
            ["NIFTY IT", 26.3, 7.1, 2.4],
            ["NIFTY METAL", 16.9, 2.8, 2.1],
            ["NIFTY ENERGY", 19.4, 2.9, 1.6],
            ["NIFTY REALTY", 48.2, 4.4, 0.3],
            ["NIFTY CONSUMER DURABLES", 62.1, 11.8, 0.5],
            ["NIFTY OIL & GAS", 15.8, 1.9, 2.6],
            ["NIFTY 500", 23.7, 4.0, 1.2],
        ],
    )
    print("\nDone. These are fixtures - replace with a real Screener.in export before use.")


if __name__ == "__main__":
    main()
