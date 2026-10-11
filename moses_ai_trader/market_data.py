from datetime import datetime, timezone
from math import isfinite

from moses_ai_trader.strategy import Bar

TIMEFRAME_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360,
    "M10": 600, "M12": 720, "M15": 900, "M20": 1200, "M30": 1800,
    "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400, "H6": 21600,
    "H8": 28800, "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000,
}


def validate_market_data(
    bars: list[Bar],
    bid: float,
    ask: float,
    point: float,
    quote_time: float,
    *,
    timeframe_seconds: int = 900,
    max_quote_age_seconds: int = 300,
    max_bar_age_seconds: int = 2700,
    max_spread_points: float = 50,
    now: float | None = None,
) -> str | None:
    """Return a block reason for unusable quotes or stale closed bars."""
    if not bars:
        return "No closed bars received"
    if not all(isfinite(value) for value in (bid, ask, point, quote_time)):
        return "Quote contains non-finite values"
    if bid <= 0 or ask <= bid or point <= 0 or timeframe_seconds <= 0:
        return "Invalid bid, ask, point, or timeframe"

    current_time = datetime.now(timezone.utc).timestamp() if now is None else now
    quote_age = current_time - quote_time
    if quote_age < -5:
        return "Quote timestamp is in the future"
    if quote_age > max_quote_age_seconds:
        return f"Stale quote ({quote_age:.0f}s old; limit {max_quote_age_seconds}s)"

    if any(not all(isfinite(value) and value > 0 for value in (
            bar.open, bar.high, bar.low, bar.close))
            or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close)
            for bar in bars):
        return "Closed bars contain invalid OHLC values"
    try:
        bar_open = datetime.fromisoformat(bars[-1].time)
        if bar_open.tzinfo is None:
            bar_open = bar_open.replace(tzinfo=timezone.utc)
        bar_close_time = bar_open.timestamp() + timeframe_seconds
    except (OverflowError, ValueError):
        return "Invalid timestamp on latest closed bar"
    bar_age = current_time - bar_close_time
    if bar_age < -timeframe_seconds:
        return "Latest bar timestamp is in the future"
    if bar_age > max_bar_age_seconds:
        return f"Stale closed bar ({bar_age:.0f}s old; limit {max_bar_age_seconds}s)"

    spread_points = (ask - bid) / point
    if spread_points > max_spread_points:
        return f"Spread too wide ({spread_points:.1f} points; limit {max_spread_points:g})"
    return None