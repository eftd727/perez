import csv
from dataclasses import dataclass
from pathlib import Path

from moses_ai_trader.risk import Instrument, position_size
from moses_ai_trader.strategy import Bar, analyze


@dataclass(frozen=True)
class BacktestConfig:
    starting_equity: float = 10_000.0
    risk_fraction: float = 0.0025
    stop_atr_multiple: float = 1.5
    reward_risk_ratio: float = 2.0
    spread_price: float = 0.00012
    slippage_price: float = 0.00002
    commission_per_lot_per_side: float = 3.5
    daily_loss_fraction: float = 0.02


def load_bars_csv(path: str | Path) -> list[Bar]:
    bars = []
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            bars.append(Bar(
                time=row["time"], open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
            ))
    if len(bars) < 60:
        raise ValueError("CSV needs at least 60 OHLC bars with time,open,high,low,close columns")
    return bars


def run_backtest(bars: list[Bar], instrument: Instrument, config: BacktestConfig = BacktestConfig(),
                 strategy_options: dict | None = None) -> dict:
    equity = config.starting_equity
    peak = equity
    max_drawdown = 0.0
    trades = []
    position = None
    daily_pnl: dict[str, float] = {}

    for index in range(60, len(bars)):
        bar = bars[index]
        day = bar.time[:10]
        if position:
            exit_price = None
            if position["side"] == "BUY":
                if bar.low <= position["stop"]:
                    exit_price = position["stop"]
                elif bar.high >= position["target"]:
                    exit_price = position["target"]
            else:
                if bar.high >= position["stop"]:
                    exit_price = position["stop"]
                elif bar.low <= position["target"]:
                    exit_price = position["target"]
            if exit_price is not None:
                direction = 1 if position["side"] == "BUY" else -1
                fill = exit_price - direction * (config.spread_price / 2 + config.slippage_price)
                gross = (fill - position["entry"]) * direction / instrument.tick_size * instrument.tick_value * position["volume"]
                net = gross - 2 * config.commission_per_lot_per_side * position["volume"]
                equity += net
                daily_pnl[day] = daily_pnl.get(day, 0.0) + net
                trades.append({"side": position["side"], "entry_time": position["time"], "exit_time": bar.time,
                               "volume": position["volume"], "net_pnl": net, "equity": equity})
                position = None

        if position is None and daily_pnl.get(day, 0.0) > -config.starting_equity * config.daily_loss_fraction:
            signal = analyze(bars[:index], **(strategy_options or {}))
            if signal.direction != "HOLD" and signal.atr > 0:
                stop_distance = signal.atr * config.stop_atr_multiple
                estimated_cost = (2 * config.commission_per_lot_per_side
                                  + 2 * config.slippage_price / instrument.tick_size * instrument.tick_value)
                volume = position_size(equity, stop_distance, instrument, config.risk_fraction, estimated_cost)
                if volume:
                    side = signal.direction
                    direction = 1 if side == "BUY" else -1
                    quote = bar.open + direction * (config.spread_price / 2 + config.slippage_price)
                    position = {"side": side, "entry": quote, "time": bar.time, "volume": volume,
                                "stop": quote - direction * stop_distance,
                                "target": quote + direction * stop_distance * config.reward_risk_ratio}

        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak else 0.0)

    wins = sum(trade["net_pnl"] > 0 for trade in trades)
    return {"starting_equity": config.starting_equity, "ending_equity": equity,
            "net_pnl": equity - config.starting_equity, "trades": trades, "trade_count": len(trades),
            "win_rate": wins / len(trades) if trades else 0.0, "max_drawdown_fraction": max_drawdown,
            "open_position": position}