"""Market prices via vnstock, with a SQLite cache and manual overrides.

All returned prices are in VND per share.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from .db import tx

log = logging.getLogger(__name__)


def _vnd(x: float) -> float:
    # vnstock history returns thousand VND; price board returns VND. Normalise defensively.
    x = float(x)
    return x * 1000 if 0 < x < 1000 else x


def _cache(conn, symbol: str, d: str, close: float, source: str) -> None:
    with tx(conn):
        conn.execute(
            "INSERT OR REPLACE INTO prices(symbol, date, close, source) VALUES (?,?,?,?)",
            (symbol, d, close, source),
        )


def set_manual_price(conn, symbol: str, price_vnd: float, d: str | None = None) -> None:
    _cache(conn, symbol.upper(), d or date.today().isoformat(), float(price_vnd), "manual")


def cached_prices(conn, symbols: list[str]) -> dict[str, tuple[float, str, str]]:
    """Latest cached {symbol: (price, date, source)}."""
    out = {}
    for s in symbols:
        r = conn.execute(
            "SELECT close, date, source FROM prices WHERE symbol=? ORDER BY date DESC, "
            "CASE source WHEN 'manual' THEN 0 ELSE 1 END LIMIT 1", (s,)
        ).fetchone()
        if r:
            out[s] = (r["close"], r["date"], r["source"])
    return out


def fetch_live_prices(conn, symbols: list[str]) -> dict[str, float]:
    """Fetch latest prices from vnstock's price board (falls back to daily history)."""
    symbols = sorted({s.upper() for s in symbols if s})
    if not symbols:
        return {}
    today = date.today().isoformat()
    got: dict[str, float] = {}
    try:
        from vnstock import Trading

        board = Trading(source="KBS").price_board(symbols)
        for r in board.itertuples():
            p = r.close_price if r.close_price and r.close_price > 0 else r.reference_price
            if p and p > 0:
                got[r.symbol] = _vnd(p)
    except Exception as e:  # network / rate-limit / API change
        log.warning("price_board failed: %s", e)

    missing = [s for s in symbols if s not in got]
    for s in missing:
        try:
            h = fetch_history(s, (date.today() - timedelta(days=14)).isoformat(), today)
            if not h.empty:
                got[s] = float(h.close.iloc[-1])
        except Exception as e:
            log.warning("history failed for %s: %s", s, e)

    for s, p in got.items():
        _cache(conn, s, today, p, "vnstock")
    return got


def fetch_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Daily close history -> DataFrame[symbol, date, close] in VND."""
    from vnstock import Quote

    df = Quote(symbol=symbol.upper(), source="VCI").history(start=start, end=end)
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "date", "close"])
    out = pd.DataFrame({
        "symbol": symbol.upper(),
        "date": pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d"),
        "close": df["close"].astype(float) * 1000,
    })
    return out


def get_prices(conn, symbols: list[str], refresh: bool = False) -> dict[str, float]:
    """Prices to value the portfolio: fresh from vnstock if refresh, else latest cache.

    A manual price for today always wins over vnstock data of the same day.
    """
    symbols = sorted({s.upper() for s in symbols if s})
    if refresh:
        fetch_live_prices(conn, symbols)
    cached = cached_prices(conn, symbols)
    return {s: v[0] for s, v in cached.items()}
