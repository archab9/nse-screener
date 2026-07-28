"""Single place symbol strings get normalised.

Before this existed the app did `.strip().upper()` in three separate places and none of
them handled exchange suffixes. Everything that takes a symbol from outside - Chartink
CSV, text file, typed input, the sector reference export - goes through normalise_symbol
so a match never fails on formatting alone.
"""

from __future__ import annotations

import re

# NSE tickers: alphanumerics plus & - . (M&M, BAJAJ-AUTO, J.K.CEMENT).
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-\.]{0,19}$")

_SUFFIXES = (".NS", ".BO", ".NSE", ".BSE", "-EQ", ".EQ")
_STRIP_CHARS = " \t\r\n'\"`,;:"


def normalise_symbol(raw: str | None) -> str:
    """Upper-case, strip quotes/whitespace, and drop exchange suffixes.

    'reliance.ns' -> 'RELIANCE',  ' "HAL" ' -> 'HAL',  'BAJAJ-AUTO' -> 'BAJAJ-AUTO'
    """
    if not raw:
        return ""
    text = str(raw).strip(_STRIP_CHARS).upper()
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip(_STRIP_CHARS)


def is_plausible_symbol(symbol: str) -> bool:
    return bool(SYMBOL_RE.match(symbol or ""))
