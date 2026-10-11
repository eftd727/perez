from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Signal:
    direction: Literal["BUY", "SELL", "HOLD"]
    reason: str
    atr: float
    fast_ma: float
    slow_ma: float
    rsi: float


def _sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def _rsi(values: list[float], period: int) -> float:
    changes = [current - previous for previous, current in zip(values, values[1:])]
    recent = changes[-period:]
    gains = sum(max(change, 0.0) for change in recent) / period
    losses = sum(max(-change, 0.0) for change in recent) / period
    if losses == 0:
        return 100.0 if gains else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _atr(bars: list[Bar], period: int) -> float:
    ranges = []
    for previous, current in zip(bars, bars[1:]):
        ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(ranges[-period:]) / period


def analyze(
    bars: list[Bar],
    fast_period: int = 20,
    slow_period: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    long_rsi: tuple[float, float] = (50.0, 70.0),
    short_rsi: tuple[float, float] = (30.0, 50.0),
) -> Signal:
    required = max(slow_period, rsi_period + 1, atr_period + 1) + 1
    if len(bars) < required:
        return Signal("HOLD", f"Need at least {required} closed bars", 0, 0, 0, 50)

    closes = [bar.close for bar in bars]
    fast_ma = _sma(closes, fast_period)
    slow_ma = _sma(closes, slow_period)
    rsi = _rsi(closes, rsi_period)
    atr = _atr(bars, atr_period)
    current, previous = bars[-1], bars[-2]

    if fast_ma > slow_ma and long_rsi[0] <= rsi <= long_rsi[1] and current.close > previous.high:
        return Signal("BUY", "Uptrend, RSI in range, and close broke prior high", atr, fast_ma, slow_ma, rsi)
    if fast_ma < slow_ma and short_rsi[0] <= rsi <= short_rsi[1] and current.close < previous.low:
        return Signal("SELL", "Downtrend, RSI in range, and close broke prior low", atr, fast_ma, slow_ma, rsi)
    return Signal("HOLD", "Entry conditions not met", atr, fast_ma, slow_ma, rsi)