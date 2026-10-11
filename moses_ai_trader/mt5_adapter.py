import sys
from contextlib import contextmanager
from datetime import datetime, timezone

from moses_ai_trader.market_data import TIMEFRAME_SECONDS, validate_market_data
from moses_ai_trader.risk import Instrument
from moses_ai_trader.strategy import Bar


@contextmanager
def demo_terminal(symbols: list[str], bars: int = 300, timeframe: str = "M15", *,
                  max_quote_age_seconds: int = 300, max_bar_age_seconds: int = 2700,
                  max_spread_points: float = 50):
    """Read closed bars from an already logged-in MT5 demo terminal."""
    if sys.platform != "win32":
        raise RuntimeError("The official MetaTrader5 Python integration runs on Windows only.")
    try:
        import MetaTrader5 as mt5
    except ImportError as error:
        raise RuntimeError("Install the MetaTrader5 package in the Windows environment.") from error

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        timeframe_id = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
        if timeframe_id is None:
            raise RuntimeError(f"Unsupported MT5 timeframe: {timeframe}")
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"MT5 account unavailable: {mt5.last_error()}")
        if account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise RuntimeError("Refusing connection: log in to a DEMO account in the MT5 terminal.")

        snapshot = {}
        for symbol in symbols:
            if not mt5.symbol_select(symbol, True):
                snapshot[symbol] = {"market_error": f"Could not select symbol: {mt5.last_error()}"}
                continue
            rates = mt5.copy_rates_from_pos(symbol, timeframe_id, 1, bars)
            info = mt5.symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if rates is None or info is None or tick is None:
                snapshot[symbol] = {"market_error": f"No complete market data: {mt5.last_error()}"}
                continue
            instrument = Instrument(info.trade_tick_size, info.trade_tick_value,
                                    info.volume_min, info.volume_max, info.volume_step)
            converted = [Bar(
                time=datetime.fromtimestamp(int(rate["time"]), timezone.utc).isoformat(),
                open=float(rate["open"]), high=float(rate["high"]),
                low=float(rate["low"]), close=float(rate["close"]),
            ) for rate in rates]
            quote_time = float(tick.time_msc) / 1000 if tick.time_msc else float(tick.time)
            bid, ask, point = float(tick.bid), float(tick.ask), float(info.point)
            snapshot[symbol] = {"bars": converted, "instrument": instrument,
                                "bid": bid, "ask": ask, "point": point,
                                "quote_time": quote_time,
                                "stops_level": float(info.trade_stops_level)}
            snapshot[symbol]["market_error"] = validate_market_data(
                converted, bid, ask, point, quote_time,
                timeframe_seconds=TIMEFRAME_SECONDS.get(timeframe, 0),
                max_quote_age_seconds=max_quote_age_seconds,
                max_bar_age_seconds=max_bar_age_seconds,
                max_spread_points=max_spread_points,
            )
        yield account, snapshot
    finally:
        mt5.shutdown()