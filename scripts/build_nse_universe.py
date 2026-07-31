"""Harvest the complete NSE sector taxonomy and its constituents from Screener.in.

    python scripts/build_nse_universe.py             # taxonomy + constituents
    python scripts/build_nse_universe.py --taxonomy  # taxonomy only, much faster

Screener publishes the NSE four-level classification as a public tree under /market/:

    Macro sector  ->  Sector  ->  Industry  ->  Basic industry  ->  companies

Every page names itself in <h1> and links its children, so the whole thing can be walked
without a login. That is the authoritative list of what exists - which matters because the
Sector Ranks tab should show every sector and subsector whether or not the bulk export
happens to price any of its members.

Writes data/sector_universe/nse_taxonomy.csv with the columns the app expects
(Sector, Subsector, Stock Name, Market Cap Category) plus the macro sector, industry and
the NSE codes.

Requests go through the same throttle the app uses, so this is polite but not fast:
the taxonomy alone is ~160 pages, with constituents ~350.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_screener.peer_ranking import cap_category  # noqa: E402
from nse_screener.stage2.screener_client import (  # noqa: E402
    BASE_URL,
    ScreenerClient,
    parse_industry_table,
)

OUT = ROOT / "data" / "sector_universe" / "nse_taxonomy.csv"
MARKET_ROOT = f"{BASE_URL}/market/"

_LINK_RE = re.compile(r'href="(/market/[^"]*)"')
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_NAME_CELL_RE = re.compile(r'href="/company/([^/"]+)/[^"]*"[^>]*>(.*?)</a>', re.S)


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


def _page_name(page: str) -> str:
    match = _H1_RE.search(page)
    if not match:
        return ""
    # Pages are titled "Chemicals Companies"; the trailing word is boilerplate.
    return re.sub(r"\s+Companies$", "", _text(match.group(1)))


def leaf_links(root_page: str) -> list[tuple[str, str]]:
    """(href, name) for every basic industry. The root page lists only leaves - the
    intermediate levels are not linked there, which is why walking by child links found
    nothing. The leaf href encodes the whole path, so the tree is reconstructed from it."""
    out = {}
    for href, label in re.findall(r'href="(/market/[^"]*)"[^>]*>(.*?)</a>', root_page, re.S):
        parts = [p for p in href.strip("/").split("/") if p][1:]
        name = _text(label)
        if len(parts) == 4 and name:
            out[href] = name
    return sorted(out.items())


def node_path(href: str, level: int) -> str:
    """Ancestor URL at the given depth: 1=macro, 2=sector, 3=industry, 4=basic."""
    parts = [p for p in href.strip("/").split("/") if p][1:]
    return "/market/" + "/".join(parts[:level]) + "/"


def walk(client: ScreenerClient, with_constituents: bool) -> list[dict]:
    started = time.time()
    root = client._request(MARKET_ROOT).text
    leaves = leaf_links(root)
    if not leaves:
        return []
    print(f"{len(leaves)} basic industries listed")

    # Name every ancestor once. Leaf names already came free from the root page.
    ancestors = sorted({node_path(h, lvl) for h, _ in leaves for lvl in (1, 2, 3)})
    print(f"naming {len(ancestors)} parent nodes...")
    names: dict[str, str] = {}
    for i, url in enumerate(ancestors, start=1):
        names[url] = _page_name(client._request(BASE_URL + url).text)
        if i % 25 == 0:
            print(f"  {i}/{len(ancestors)}")

    rows: list[dict] = []
    for i, (href, basic_name) in enumerate(leaves, start=1):
        common = {
            "Macro Sector": names.get(node_path(href, 1), ""),
            "Sector": names.get(node_path(href, 2), ""),
            "Industry": names.get(node_path(href, 3), ""),
            "Subsector": basic_name,
            "Code": href.strip("/").split("/")[-1],
        }
        if not with_constituents:
            rows.append({**common, "Stock Name": "", "Symbol": "", "Market Cap Category": ""})
            continue

        page = client._request(BASE_URL + href).text
        members = parse_industry_table(page)
        company_names = dict(_NAME_CELL_RE.findall(page))
        if not members:
            rows.append({**common, "Stock Name": "", "Symbol": "", "Market Cap Category": ""})
        for symbol, market_cap in members:
            rows.append({
                **common,
                "Stock Name": _text(company_names.get(symbol, symbol)),
                "Symbol": symbol,
                "Market Cap Category": cap_category(market_cap) if market_cap else "",
            })
        if i % 25 == 0:
            print(f"  constituents {i}/{len(leaves)}")

    print(f"walked in {time.time() - started:.0f}s")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--taxonomy", action="store_true",
                        help="skip constituent tables (far fewer requests)")
    args = parser.parse_args()

    client = ScreenerClient()
    rows = walk(client, with_constituents=not args.taxonomy)
    if not rows:
        print("nothing harvested", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["Macro Sector", "Sector", "Industry", "Subsector", "Code",
              "Stock Name", "Symbol", "Market Cap Category"]
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    sectors = {r["Sector"] for r in rows if r["Sector"]}
    subs = {r["Subsector"] for r in rows if r["Subsector"]}
    stocks = {r["Symbol"] for r in rows if r["Symbol"]}
    print(f"{OUT.relative_to(ROOT)}: {len(sectors)} sectors, {len(subs)} subsectors, "
          f"{len(stocks)} companies, {client.rate_limit_hits} x 429")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
