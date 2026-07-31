"""Kite Connect wrapper - live quotes and historical OHLCV (spec section 9).

Credential handling, deliberately narrow:
  - api_key / api_secret are read from the environment or Windows Credential Manager.
    They are never written to this repo and never requested through a chat.
  - The login step is the standard Kite browser flow. The user logs in at Zerodha, is
    redirected to a URL carrying a request_token, and pastes that URL back. This module
    exchanges the request_token for an access_token. It never sees, asks for, or stores a
    Zerodha password.

kiteconnect is imported lazily so the rest of the pipeline runs, and the GUI still opens,
on a machine where it is not installed yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from urllib.parse import parse_qs, urlparse

from ..config import (
    kite_access_token,
    kite_api_key,
    kite_api_secret,
    store_kite_access_token,
)

LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"


class TokenState(Enum):
    VALID = "valid"
    MISSING = "missing"
    EXPIRED = "expired"
    NO_API_KEY = "no_api_key"
    LIBRARY_MISSING = "library_missing"

    @property
    def needs_login(self) -> bool:
        return self in (TokenState.MISSING, TokenState.EXPIRED)


class KiteError(RuntimeError):
    pass


@dataclass
class TokenCheck:
    state: TokenState
    message: str

    @property
    def ok(self) -> bool:
        return self.state is TokenState.VALID


def build_login_url() -> str | None:
    api_key = kite_api_key()
    return LOGIN_URL_TEMPLATE.format(api_key=api_key) if api_key else None


def check_token() -> TokenCheck:
    """Validate today's access_token with a cheap authenticated call.

    Kite access tokens expire daily, so this runs on every button press (spec section 10).
    """
    api_key = kite_api_key()
    if not api_key:
        return TokenCheck(
            TokenState.NO_API_KEY,
            "No Kite API key found. Set KITE_API_KEY, or store it in Windows Credential "
            "Manager under service 'nse-screener-kite'.",
        )

    token = kite_access_token()
    if not token:
        return TokenCheck(TokenState.MISSING, "No Kite access token for today - login required.")

    try:
        from kiteconnect import KiteConnect
    except ImportError:
        return TokenCheck(
            TokenState.LIBRARY_MISSING,
            "kiteconnect is not installed - run: pip install kiteconnect",
        )

    try:
        client = KiteConnect(api_key=api_key)
        client.set_access_token(token)
        client.profile()  # cheapest authenticated endpoint
        return TokenCheck(TokenState.VALID, "Kite access token is valid.")
    except Exception as exc:  # kiteconnect raises a family of exception types
        return TokenCheck(TokenState.EXPIRED, f"Kite access token rejected ({exc}) - login required.")


def extract_request_token(redirect_url: str) -> str | None:
    """Pull request_token out of the URL Zerodha redirects to after login."""
    try:
        params = parse_qs(urlparse(redirect_url.strip()).query)
    except ValueError:
        return None
    values = params.get("request_token")
    return values[0] if values else None


def complete_login(redirect_url: str) -> TokenCheck:
    """Exchange a request_token for an access_token and persist it."""
    api_key, api_secret = kite_api_key(), kite_api_secret()
    if not api_key or not api_secret:
        return TokenCheck(TokenState.NO_API_KEY, "Kite API key and secret must both be configured.")

    request_token = extract_request_token(redirect_url)
    if not request_token:
        return TokenCheck(
            TokenState.MISSING,
            "No request_token found in that URL. Paste the full URL you were redirected to.",
        )

    try:
        from kiteconnect import KiteConnect

        client = KiteConnect(api_key=api_key)
        data = client.generate_session(request_token, api_secret=api_secret)
        token = data["access_token"]
    except ImportError:
        return TokenCheck(TokenState.LIBRARY_MISSING, "kiteconnect is not installed.")
    except Exception as exc:
        return TokenCheck(TokenState.EXPIRED, f"Login exchange failed: {exc}")

    stored = store_kite_access_token(token)
    suffix = "" if stored else " (could not persist to Credential Manager - set KITE_ACCESS_TOKEN)"
    return TokenCheck(TokenState.VALID, f"Kite login complete.{suffix}")


class KiteSession:
    """Thin wrapper over KiteConnect for the two jobs spec section 9 assigns it."""

    def __init__(self) -> None:
        self._client = None

    def _connect(self):
        if self._client is not None:
            return self._client
        api_key, token = kite_api_key(), kite_access_token()
        if not api_key or not token:
            raise KiteError("Kite is not authenticated.")
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise KiteError("kiteconnect is not installed.") from exc
        client = KiteConnect(api_key=api_key)
        client.set_access_token(token)
        self._client = client
        return client

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Live quotes for the stage-2 shortlist, as a pre-output sanity check."""
        if not symbols:
            return {}
        client = self._connect()
        instruments = [f"NSE:{s.strip().upper()}" for s in symbols]
        try:
            raw = client.quote(instruments)
        except Exception as exc:
            raise KiteError(f"Kite quote request failed: {exc}") from exc
        return {key.split(":", 1)[1]: value for key, value in raw.items()}

    def last_prices(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for symbol, payload in self.quotes(symbols).items():
            price = payload.get("last_price")
            if price is not None:
                out[symbol] = float(price)
        return out

    def instrument_token(self, symbol: str) -> int | None:
        client = self._connect()
        try:
            for row in client.instruments("NSE"):
                if row.get("tradingsymbol", "").upper() == symbol.strip().upper():
                    return int(row["instrument_token"])
        except Exception as exc:
            raise KiteError(f"Kite instrument lookup failed: {exc}") from exc
        return None

    def historical_daily(self, symbol: str, start: date, end: date) -> list[dict]:
        """Daily OHLCV candles for the backtest (spec section 11)."""
        token = self.instrument_token(symbol)
        if token is None:
            raise KiteError(f"No NSE instrument token for {symbol}.")
        client = self._connect()
        try:
            return client.historical_data(
                token,
                datetime.combine(start, datetime.min.time()),
                datetime.combine(end, datetime.min.time()),
                interval="day",
            )
        except Exception as exc:
            raise KiteError(f"Kite historical request failed for {symbol}: {exc}") from exc
