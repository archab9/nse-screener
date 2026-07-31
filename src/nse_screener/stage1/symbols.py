"""Stage-1 symbol input beyond the Chartink CSV.

Three ways in, all producing the same Stage1Hit list the rest of the pipeline expects:
  - a Chartink CSV export        (csv_import.load_chartink_csv)
  - a plain text file            one symbol per line
  - symbols typed or pasted      commas, spaces or newlines

Text and manual input carry no price data at all, so every hit comes back flagged
price_backfilled - the pipeline fills close from Kite where it can.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..models import Stage1Hit

# NSE symbols are alphanumerics with the occasional & or -, e.g. M&M, BAJAJ-AUTO.
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-\.]{0,19}$")

# Common header words to drop when a text file has been pasted from a table.
_HEADER_WORDS = {"SYMBOL", "SYMBOLS", "TICKER", "STOCK", "NAME", "SR", "SRNO", "NO"}


class SymbolInputError(RuntimeError):
    pass


def parse_symbols(text: str) -> tuple[list[str], list[str]]:
    """Split free text into (accepted symbols, rejected tokens).

    Rejected tokens are returned rather than silently dropped, so the UI can tell the user
    exactly what it ignored instead of quietly screening a shorter list than they meant.
    """
    tokens = [t.strip().upper() for t in re.split(r"[,\s;|]+", text or "") if t.strip()]
    accepted: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        cleaned = token.strip(".,;'\"")
        if not cleaned or cleaned in _HEADER_WORDS:
            continue
        if not _SYMBOL_RE.match(cleaned):
            rejected.append(token)
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        accepted.append(cleaned)

    return accepted, rejected


def load_symbol_file(path: Path | str) -> tuple[list[Stage1Hit], list[str]]:
    """Read a plain text file of symbols, one per line (or comma separated)."""
    path = Path(path)
    if not path.exists():
        raise SymbolInputError(f"Symbol file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SymbolInputError(f"Could not read {path.name}: {exc}") from exc

    symbols, rejected = parse_symbols(text)
    scan_date = date.fromtimestamp(path.stat().st_mtime)
    return [_hit(s, scan_date) for s in symbols], rejected


def hits_from_text(text: str) -> tuple[list[Stage1Hit], list[str]]:
    """Build hits from symbols typed or pasted into the app."""
    symbols, rejected = parse_symbols(text)
    return [_hit(s, date.today()) for s in symbols], rejected


def _hit(symbol: str, scan_date: date) -> Stage1Hit:
    return Stage1Hit(symbol=symbol, name="", scan_date=scan_date, price_backfilled=True)
