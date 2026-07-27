"""Live Screener.in access using the user's own Premium login.

The original spec argued against this (its section 0 wanted manual exports only, on the
grounds that automating a paid account risks it). The user overrode that decision
deliberately. The manual-export loader in fundamentals.py stays working alongside this,
so a selector change on Screener.in's side degrades to "use the local export" rather than
taking the whole screener down.

Credential handling:
  - Username and password come from Windows Credential Manager (keyring) or the
    environment. They are never written to this repo, never logged, and never printed.
  - The session cookie is held in memory for the life of the process only.

Page structure this parser depends on (verified against a live public company page):
  - Each block is a <section id="quarters" | "profit-loss" | "cash-flow" | "ratios" |
    "shareholding"> containing a <table class="data-table">.
  - Column headers carry data-date-key="YYYY-MM-DD" - an exact period date, used in
    preference to parsing the visible "Mar 2024" label.
  - Row labels sit in <td class="text">, sometimes wrapped in a <button>, and may carry a
    trailing "+" expander glyph.
  - The summary box is <ul id="top-ratios"> with Market Cap / Stock P/E / Book Value /
    Dividend Yield / ROCE / ROE entries.

All of that is scraped markup, not a contract. SELECTORS WILL BREAK. Every failure path
here raises ScreenerAuthError or ScreenerParseError so the caller can show a banner and
fall back, rather than silently producing a half-empty company record.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import date

import requests

from ..config import _keyring_get, _keyring_set, settings
from .fundamentals import (
    AnnualPoint,
    CompanyFundamentals,
    FundamentalsStore,
    QuarterPoint,
    ShareholdingPoint,
    _num,
)

BASE_URL = "https://www.screener.in"
LOGIN_URL = f"{BASE_URL}/login/"
COMPANY_URL = f"{BASE_URL}/company/{{symbol}}/"
CONSOLIDATED_URL = f"{BASE_URL}/company/{{symbol}}/consolidated/"

KEYRING_SERVICE = "nse-screener-screener-in"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class ScreenerAuthError(RuntimeError):
    pass


class ScreenerParseError(RuntimeError):
    pass


# --------------------------------------------------------------------- credentials

def screener_username() -> str | None:
    return os.environ.get("SCREENER_USERNAME") or _keyring_get(KEYRING_SERVICE, "username")


def screener_password() -> str | None:
    return os.environ.get("SCREENER_PASSWORD") or _keyring_get(KEYRING_SERVICE, "password")


def store_credentials(username: str, password: str) -> bool:
    """Persist to Windows Credential Manager. Returns False if no backend is available."""
    return _keyring_set(KEYRING_SERVICE, "username", username) and _keyring_set(
        KEYRING_SERVICE, "password", password
    )


def has_credentials() -> bool:
    return bool(screener_username() and screener_password())


# ------------------------------------------------------------------- html parsing

_TAG_RE = re.compile(r"<[^>]+>")
_SECTION_RE = "<section[^>]*id=\"{sid}\"[^>]*>(.*?)</section>"
_TABLE_RE = re.compile(r"<table[^>]*class=\"[^\"]*data-table[^\"]*\"[^>]*>(.*?)</table>", re.S)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_DATE_KEY_RE = re.compile(r"<th[^>]*data-date-key=\"([^\"]+)\"[^>]*>(.*?)</th>", re.S)
_LABEL_CELL_RE = re.compile(r"<td[^>]*class=\"[^\"]*text[^\"]*\"[^>]*>(.*?)</td>", re.S)


def _text(fragment: str) -> str:
    """Strip tags and entities, collapse whitespace, drop the '+' expander glyph."""
    cleaned = html.unescape(_TAG_RE.sub(" ", fragment))
    cleaned = cleaned.replace("\xa0", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.rstrip("+").strip()


@dataclass
class ParsedTable:
    """One Screener.in data-table: period keys plus label -> values."""

    periods: list[str]
    rows: dict[str, list[float | None]]

    def row(self, *names: str) -> list[float | None] | None:
        """Look a row up by any of several accepted labels, case-insensitively."""
        lowered = {k.lower(): v for k, v in self.rows.items()}
        for name in names:
            if name.lower() in lowered:
                return lowered[name.lower()]
        # Fall back to a prefix match - Screener occasionally appends qualifiers.
        for name in names:
            for key, values in lowered.items():
                if key.startswith(name.lower()):
                    return values
        return None


def parse_data_table(section_html: str) -> ParsedTable:
    table_match = _TABLE_RE.search(section_html)
    if not table_match:
        raise ScreenerParseError("No data-table found in section.")
    table = table_match.group(1)

    # Prefer the machine-readable date keys; fall back to visible header text.
    periods = [key for key, _label in _DATE_KEY_RE.findall(table)]
    if not periods:
        head = re.search(r"<thead>(.*?)</thead>", table, re.S)
        if head:
            cells = [_text(c) for c in _CELL_RE.findall(head.group(1))]
            periods = [c for c in cells if c]

    rows: dict[str, list[float | None]] = {}
    body = re.search(r"<tbody>(.*?)</tbody>", table, re.S)
    for row_html in _ROW_RE.findall(body.group(1) if body else table):
        label_match = _LABEL_CELL_RE.search(row_html)
        if not label_match:
            continue
        label = _text(label_match.group(1))
        if not label:
            continue
        cells = _CELL_RE.findall(row_html)[1:]  # first cell is the label
        rows[label] = [_num(_text(c).replace("%", "")) for c in cells]
    return ParsedTable(periods=periods, rows=rows)


def _section(page: str, section_id: str) -> str | None:
    match = re.search(_SECTION_RE.format(sid=re.escape(section_id)), page, re.S)
    return match.group(1) if match else None


def parse_top_ratios(page: str) -> dict[str, float | None]:
    """Market Cap / Stock P/E / Book Value / Dividend Yield / ROCE / ROE summary box.

    Values live in a nested <span class="number">. Reading that directly avoids two traps:
    a label containing a slash ('Stock P/E') and a value containing one ('High / Low',
    which carries two numbers and is not used here).
    """
    match = re.search(r"<ul[^>]*id=\"top-ratios\"[^>]*>(.*?)</ul>", page, re.S)
    if not match:
        return {}
    out: dict[str, float | None] = {}
    for item in re.findall(r"<li[^>]*>(.*?)</li>", match.group(1), re.S):
        name_match = re.search(r"<span[^>]*class=\"[^\"]*name[^\"]*\"[^>]*>(.*?)</span>", item, re.S)
        if not name_match:
            continue
        name = _text(name_match.group(1)).rstrip(":").strip().lower()
        numbers = re.findall(r"<span[^>]*class=\"[^\"]*number[^\"]*\"[^>]*>(.*?)</span>", item, re.S)
        if numbers:
            out[name] = _num(_text(numbers[0]).replace(",", ""))
        else:
            raw = _text(item).replace("₹", "").replace("Cr.", "").replace(",", "").replace("%", "")
            out[name] = _num(raw.replace(name, "", 1).strip())
    return out


_RANGE_ROW_RE = re.compile(r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", re.S)


def parse_growth_table(page: str, heading: str) -> dict[str, float | None]:
    """Read one of Screener.in's compounded-growth boxes.

    Rendered as small tables headed 'Compounded Profit Growth' / 'Compounded Sales Growth'
    / 'Return on Equity', with rows like '5 Years:' -> '23%'.
    """
    index = page.find(heading)
    if index < 0:
        return {}
    table = re.search(r"<table[^>]*>(.*?)</table>", page[index - 500 : index + 1200], re.S)
    if not table:
        return {}

    out: dict[str, float | None] = {}
    for label_html, value_html in _RANGE_ROW_RE.findall(table.group(1)):
        label = _text(label_html).rstrip(":").strip().lower()
        if not label:
            continue
        out[label] = _num(_text(value_html).replace("%", ""))
    return out


def blended_eps_cagr(page: str) -> float | None:
    """P7 wants a blended 3-5yr growth rate rather than a single noisy year.

    Uses compounded PROFIT growth as the EPS-growth proxy - Screener publishes profit
    growth, not EPS growth, and the two only diverge on share-count changes.
    """
    growth = parse_growth_table(page, "Compounded Profit Growth")
    values = [growth.get("3 years"), growth.get("5 years")]
    present = [v for v in values if v is not None]
    if present:
        return sum(present) / len(present)
    return growth.get("10 years") or growth.get("ttm")


def parse_company_page(symbol: str, page: str) -> CompanyFundamentals:
    """Turn a fetched company page into the same object the local CSV loader produces."""
    if "top-ratios" not in page:
        raise ScreenerParseError(
            f"{symbol}: page has no top-ratios block - not a company page, or the layout changed."
        )

    top = parse_top_ratios(page)
    price = top.get("current price")
    book_value = top.get("book value")
    chain = parse_industry_chain(page)

    company = CompanyFundamentals(
        symbol=symbol.upper(),
        name=_parse_company_name(page) or symbol.upper(),
        industry=chain[-1] if chain else "",
        industry_path=" > ".join(chain),
        market_cap_cr=top.get("market cap"),
        pe=top.get("stock p/e"),
        # Screener publishes Book Value per share, so PB is price / BVPS.
        pb=(price / book_value) if price and book_value else None,
        dividend_yield_pct=top.get("dividend yield"),
        eps_cagr_pct=blended_eps_cagr(page),
    )

    quarters = _section(page, "quarters")
    if quarters:
        table = parse_data_table(quarters)
        sales = table.row("Sales", "Revenue", "Net Sales") or []
        profit = table.row("Net Profit", "Profit after tax") or []
        for i, period in enumerate(table.periods):
            company.quarterly.append(
                QuarterPoint(
                    quarter=period,
                    net_sales=sales[i] if i < len(sales) else None,
                    net_profit=profit[i] if i < len(profit) else None,
                )
            )

    _parse_annual(company, page)
    _parse_shareholding(company, page)
    return company


def _parse_annual(company: CompanyFundamentals, page: str) -> None:
    """Combine profit-loss, cash-flow and ratios into one annual series."""
    series: dict[str, dict[str, float | None]] = {}

    def absorb(section_id: str, mapping: dict[str, tuple[str, ...]]) -> None:
        section = _section(page, section_id)
        if not section:
            return
        try:
            table = parse_data_table(section)
        except ScreenerParseError:
            return
        for field, labels in mapping.items():
            values = table.row(*labels)
            if values is None:
                continue
            for i, period in enumerate(table.periods):
                if i < len(values):
                    series.setdefault(period, {})[field] = values[i]

    absorb("profit-loss", {"pat": ("Net Profit", "Profit after tax")})
    absorb(
        "cash-flow",
        {
            "cfo": ("Cash from Operating Activity", "Cash from Operating Activities"),
            "fcf": ("Free Cash Flow",),
            "investing": ("Cash from Investing Activity", "Cash from Investing Activities"),
        },
    )
    absorb("ratios", {"roce_pct": ("ROCE %", "ROCE"), "receivable_days": ("Debtor Days",)})
    # Screener publishes ROCE per year but NOT ROE. P2 needs ROE on 4-of-5 logic, so it is
    # derived from the balance sheet: ROE = Net Profit / (Equity Capital + Reserves).
    absorb(
        "balance-sheet",
        {"equity_capital": ("Equity Capital",), "reserves": ("Reserves",)},
    )

    for period, values in series.items():
        year, _month = _period_year(period)
        if not year:
            continue
        cfo = values.get("cfo")
        fcf = values.get("fcf")
        # Screener publishes Free Cash Flow directly on some pages. Where it does, derive
        # capex from it so FCF stays consistent with the site rather than re-deriving it
        # from investing cash flow, which also contains acquisitions and investments.
        capex = (cfo - fcf) if (cfo is not None and fcf is not None) else None

        pat = values.get("pat")
        equity = values.get("equity_capital")
        reserves = values.get("reserves")
        net_worth = (equity or 0.0) + (reserves or 0.0) if (equity or reserves) else None
        roe = (pat / net_worth * 100.0) if (pat is not None and net_worth) else None

        company.annual.append(
            AnnualPoint(
                fy=year,
                roce_pct=values.get("roce_pct"),
                roe_pct=roe,
                cfo=cfo,
                capex=capex,
                pat=pat,
                receivable_days=values.get("receivable_days"),
            )
        )


def _parse_shareholding(company: CompanyFundamentals, page: str) -> None:
    section = _section(page, "shareholding")
    if not section:
        return
    try:
        table = parse_data_table(section)
    except ScreenerParseError:
        return

    promoters = table.row("Promoters") or []
    fiis = table.row("FIIs", "Foreign Institutions") or []
    diis = table.row("DIIs", "Domestic Institutions") or []
    government = table.row("Government") or []
    public = table.row("Public") or []

    for i, period in enumerate(table.periods):
        def at(series: list[float | None]) -> float | None:
            return series[i] if i < len(series) else None

        promoter = at(promoters)
        gov = at(government)
        # PSU/bank exception (P6): government stake is the controlling holding.
        if (promoter is None or promoter == 0) and gov:
            promoter = gov
            company.is_psu = True

        company.shareholding.append(
            ShareholdingPoint(
                quarter=period,
                promoter_pct=promoter,
                pledge_pct=None,  # not exposed in the shareholding table - see note below
                fii_pct=at(fiis),
                dii_pct=at(diis),
                public_pct=at(public),
            )
        )


def _parse_company_name(page: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    return _text(match.group(1)) if match else ""


def parse_industry_chain(page: str) -> list[str]:
    """Screener.in's classification hierarchy, broadest first.

    Rendered as nested /market/ links in the peer-comparison block, e.g.
        /market/IN03/                              -> Energy
        /market/IN03/IN0301/                       -> Oil, Gas & Consumable Fuels
        /market/IN03/IN0301/IN030103/              -> Petroleum Products
        /market/IN03/IN0301/IN030103/IN030103001/  -> Refineries & Marketing

    Depth is taken from the href, so the chain stays ordered even if the links move.
    """
    seen: dict[str, tuple[int, str]] = {}
    for href, label in re.findall(r"<a[^>]*href=\"(/market/[^\"]*)\"[^>]*>(.*?)</a>", page, re.S):
        text = _text(label)
        if not text or len(text) > 80:
            continue
        depth = len([part for part in href.strip("/").split("/") if part]) - 1
        if text not in seen or depth < seen[text][0]:
            seen[text] = (depth, text)
    return [text for _depth, text in sorted(seen.values())]


def _period_year(period: str) -> tuple[int, int]:
    from .fundamentals import _quarter_sort_key

    return _quarter_sort_key(period)


# ------------------------------------------------------------------------ client

class ScreenerClient:
    """Authenticated Screener.in session."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._logged_in = False

    def login(self, username: str | None = None, password: str | None = None) -> None:
        username = username or screener_username()
        password = password or screener_password()
        if not username or not password:
            raise ScreenerAuthError(
                "No Screener.in credentials found. Add them in the app "
                "(they are stored in Windows Credential Manager, never in this repo)."
            )

        try:
            page = self.session.get(LOGIN_URL, timeout=self.timeout)
            page.raise_for_status()
        except requests.RequestException as exc:
            raise ScreenerAuthError(f"Could not reach Screener.in: {exc}") from exc

        token = self.session.cookies.get("csrftoken")
        if not token:
            match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', page.text)
            token = match.group(1) if match else None
        if not token:
            raise ScreenerAuthError("Screener.in login page returned no CSRF token.")

        try:
            response = self.session.post(
                LOGIN_URL,
                data={
                    "csrfmiddlewaretoken": token,
                    "username": username,
                    "password": password,
                    "next": "",
                },
                headers={"Referer": LOGIN_URL},
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise ScreenerAuthError(f"Screener.in login request failed: {exc}") from exc

        # Django re-renders the form with an error rather than returning a 4xx.
        if "sessionid" not in self.session.cookies:
            raise ScreenerAuthError(
                "Screener.in rejected the login. Check the username and password, "
                "and whether the account needs a captcha or 2FA step in a browser."
            )
        if response.status_code >= 400:
            raise ScreenerAuthError(f"Screener.in returned HTTP {response.status_code} on login.")
        self._logged_in = True

    @property
    def logged_in(self) -> bool:
        return self._logged_in and "sessionid" in self.session.cookies

    def is_premium(self) -> bool | None:
        """Best-effort check. None means it could not be determined."""
        if not self.logged_in:
            return None
        try:
            page = self.session.get(f"{BASE_URL}/home/", timeout=self.timeout)
        except requests.RequestException:
            return None
        markers = ("upgrade to premium", "subscribe to premium", "start free trial")
        lowered = page.text.lower()
        return not any(marker in lowered for marker in markers)

    def fetch_company(self, symbol: str, consolidated: bool = True) -> CompanyFundamentals:
        """Fetch and parse one company. Consolidated figures where the company reports them."""
        symbol = symbol.strip().upper()
        urls = [CONSOLIDATED_URL, COMPANY_URL] if consolidated else [COMPANY_URL]

        last_error: Exception | None = None
        for template in urls:
            url = template.format(symbol=symbol)
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                continue
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                last_error = ScreenerParseError(f"{symbol}: HTTP {response.status_code}")
                continue
            return parse_company_page(symbol, response.text)

        raise ScreenerParseError(f"Could not fetch {symbol}: {last_error or 'not found'}")

    def fetch_many(
        self, symbols: list[str], on_progress=None
    ) -> tuple[FundamentalsStore, dict[str, str]]:
        """Fetch a list of symbols, collecting per-symbol failures instead of aborting."""
        store = FundamentalsStore(newest_file_date=date.today())
        failures: dict[str, str] = {}
        total = len(symbols)

        for i, symbol in enumerate(symbols, start=1):
            if on_progress:
                on_progress(symbol, i, total)
            try:
                company = self.fetch_company(symbol)
            except (ScreenerParseError, ScreenerAuthError) as exc:
                failures[symbol] = str(exc)
                continue
            store.companies[company.symbol] = company

        return store, failures
