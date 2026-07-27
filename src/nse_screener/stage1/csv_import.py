"""Manual mode - read a CSV exported from the Chartink UI.

The real export (confirmed against the user's 'VOLUME SCAN.csv') carries only:

    Sr., Stock Name, Symbol

Spec section 2 lists close / %change / volume / date as stage-1 outputs. Those columns
simply are not in the manual export, so they come back as None and get backfilled from
Kite quotes in the pipeline. The scan date is unknown too, so the file's own mtime is
used as the best available proxy and reported as such.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from ..models import Stage1Hit

# Accept a few spellings so a re-export with slightly different headers still loads.
SYMBOL_HEADERS = ("symbol", "nsecode", "nse code", "ticker")
NAME_HEADERS = ("stock name", "name", "company", "company name")
CLOSE_HEADERS = ("close", "close price", "ltp", "price")
PCT_HEADERS = ("% chg", "%chg", "per chg", "change %", "% change", "chg %")
VOLUME_HEADERS = ("volume", "vol")


class Stage1CsvError(RuntimeError):
    pass


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for key, value in row.items():
        if key is None:
            continue
        if key.strip().lower().rstrip(".") in candidates:
            return value
    return None


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.strip().replace(",", "").replace("%", "").replace("₹", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_int(raw: str | None) -> int | None:
    value = _to_float(raw)
    return int(value) if value is not None else None


def load_chartink_csv(path: Path | str) -> list[Stage1Hit]:
    """Parse a Chartink CSV export into stage-1 hits.

    Returns an empty list for an empty scan result - that is a legitimate quiet-market
    outcome, not an error (spec sections 2 and 7).
    """
    path = Path(path)
    if not path.exists():
        raise Stage1CsvError(f"Chartink CSV not found: {path}")

    scan_date = date.fromtimestamp(path.stat().st_mtime)

    hits: list[Stage1Hit] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return []

        headers = {h.strip().lower().rstrip(".") for h in reader.fieldnames if h}
        if not headers & set(SYMBOL_HEADERS):
            raise Stage1CsvError(
                f"No symbol column in {path.name}. Found: {sorted(headers)}. "
                f"Expected one of: {list(SYMBOL_HEADERS)}"
            )

        for row in reader:
            symbol = (_pick(row, SYMBOL_HEADERS) or "").strip().upper()
            if not symbol:
                continue

            close = _to_float(_pick(row, CLOSE_HEADERS))
            pct = _to_float(_pick(row, PCT_HEADERS))
            volume = _to_int(_pick(row, VOLUME_HEADERS))

            hits.append(
                Stage1Hit(
                    symbol=symbol,
                    name=(_pick(row, NAME_HEADERS) or "").strip(),
                    close=close,
                    pct_change=pct,
                    volume=volume,
                    scan_date=scan_date,
                    price_backfilled=close is None,
                )
            )
    return hits


def csv_age_days(path: Path | str, today: date | None = None) -> int | None:
    path = Path(path)
    if not path.exists():
        return None
    today = today or date.today()
    modified = datetime.fromtimestamp(path.stat().st_mtime).date()
    return (today - modified).days
