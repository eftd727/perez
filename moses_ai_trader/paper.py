import csv
import json
import logging
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path

from moses_ai_trader.risk import Instrument, position_size
from moses_ai_trader.strategy import Signal


class PaperBook:
    def __init__(self, state_path: str, trade_log_path: str, initial_equity: float):
        self.state_path = Path(state_path)
        self.trade_log_path = Path(trade_log_path)
        self.initial_equity = initial_equity
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._save({"positions": [], "closed_trades": [], "daily_pnl": {},
                        "daily_start_equity": {}, "daily_peak_equity": {},
                        "daily_start_floating": {}, "daily_halts": {}, "daily_status": {}})

    def _load(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save(self, state: dict) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def _log_trade(self, record: dict) -> None:
        fields = ["time", "event", "symbol", "side", "volume", "price", "pnl", "reason"]
        new_file = not self.trade_log_path.exists()
        with self.trade_log_path.open("a", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            if new_file:
                writer.writeheader()
            writer.writerow({key: record.get(key, "") for key in fields})

    @staticmethod
    def _valid_levels(side: str, entry: float, stop: float, target: float) -> bool:
        if not all(isfinite(value) and value > 0 for value in (entry, stop, target)):
            return False
        if side == "BUY":
            return stop < entry < target
        if side == "SELL":
            return target < entry < stop
        return False

    @staticmethod
    def _position_pnl(position: dict, market: dict, commission_per_lot_per_side: float,
                      slippage_points: float) -> float:
        instrument = market["instrument"]
        direction = 1 if position["side"] == "BUY" else -1
        liquidation_quote = market["bid"] if direction == 1 else market["ask"]
        gross = ((liquidation_quote - position["entry"]) * direction / instrument.tick_size
                 * instrument.tick_value * position["volume"])
        exit_slippage = slippage_points * market["point"] / instrument.tick_size * instrument.tick_value
        return gross - (2 * commission_per_lot_per_side + exit_slippage) * position["volume"]

    @staticmethod
    def _market_reason(data: dict | None, max_spread_points: float) -> str | None:
        if data is None:
            return "No market snapshot available"
        if data.get("market_error"):
            return str(data["market_error"])
        try:
            bid, ask, point = float(data["bid"]), float(data["ask"]), float(data["point"])
        except (KeyError, TypeError, ValueError):
            return "Market snapshot is incomplete"
        if not all(isfinite(value) for value in (bid, ask, point)) or bid <= 0 or ask <= bid or point <= 0:
            return "Invalid bid/ask/point; entry blocked"
        spread_points = (ask - bid) / point
        if spread_points > max_spread_points:
            return f"Spread too wide ({spread_points:.1f} points; limit {max_spread_points:g})"
        instrument = data.get("instrument")
        if not isinstance(instrument, Instrument) or not all(isfinite(value) and value > 0 for value in (
                instrument.tick_size, instrument.tick_value, instrument.volume_min,
                instrument.volume_max, instrument.volume_step)):
            return "Invalid broker tick or volume metadata"
        if instrument.volume_max < instrument.volume_min:
            return "Invalid broker volume range"
        return None

    def process_scan(
        self,
        markets: dict[str, dict],
        signals: dict[str, Signal],
        demo_equity: float,
        *,
        daily_loss_fraction: float,
        max_daily_drawdown_fraction: float,
        max_open_trades: int,
        stop_atr_multiple: float,
        reward_risk_ratio: float,
        risk_fraction: float,
        commission_per_lot_per_side: float,
        slippage_points: float,
        max_spread_points: float,
        now: datetime | None = None,
    ) -> dict:
        state = self._load()
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment = moment.astimezone(timezone.utc)
        timestamp = moment.isoformat()
        day = moment.date().isoformat()
        for key in ("daily_pnl", "daily_start_equity", "daily_peak_equity", "daily_start_floating",
                    "daily_halts", "daily_status"):
            state.setdefault(key, {})
        configured_values = (daily_loss_fraction, max_daily_drawdown_fraction,
                             commission_per_lot_per_side, slippage_points, max_spread_points)
        config_valid = (all(isinstance(value, (int, float)) and isfinite(value) for value in configured_values)
                        and daily_loss_fraction >= 0 and max_daily_drawdown_fraction >= 0
                        and commission_per_lot_per_side >= 0 and slippage_points >= 0
                        and max_spread_points >= 0 and isinstance(max_open_trades, int)
                        and max_open_trades >= 0)
        if not config_valid:
            reason = "Invalid risk-control configuration; new simulated trades blocked"
            state["daily_status"][day] = {"updated_at": timestamp, "blocked": True,
                                          "reason": reason,
                                          "symbols": {symbol: reason for symbol in signals}}
            self._save(state)
            return state["daily_status"][day]
        if not isfinite(demo_equity) or demo_equity <= 0:
            reason = "Invalid demo account equity; new simulated trades blocked"
            state["daily_status"][day] = {"updated_at": timestamp, "blocked": True,
                                          "reason": reason,
                                          "symbols": {symbol: reason for symbol in signals}}
            self._save(state)
            return state["daily_status"][day]

        start_equity = state["daily_start_equity"].setdefault(day, demo_equity)
        state["daily_peak_equity"].setdefault(day, start_equity)
        market_reasons = {symbol: self._market_reason(data, max_spread_points)
                          for symbol, data in markets.items()}
        unavailable_position = None

        if state["daily_start_floating"].get(day) is None:
            start_floating = 0.0
            can_set_floating_baseline = True
            for position in state["positions"]:
                market = markets.get(position["symbol"])
                if (market_reasons.get(position["symbol"], "No market snapshot")
                        or not self._valid_levels(position.get("side"), position.get("entry", 0),
                                                  position.get("stop", 0), position.get("target", 0))):
                    can_set_floating_baseline = False
                    break
                start_floating += self._position_pnl(position, market, commission_per_lot_per_side,
                                                     slippage_points)
            if can_set_floating_baseline:
                state["daily_start_floating"][day] = start_floating

        for position in list(state["positions"]):
            symbol = position["symbol"]
            market = markets.get(symbol)
            reason = market_reasons.get(symbol, "No fresh market snapshot for open position")
            if reason:
                unavailable_position = f"Cannot value open {symbol} position: {reason}"
                continue
            side = position.get("side")
            if (not self._valid_levels(side, position.get("entry", 0), position.get("stop", 0), position.get("target", 0))
                    or not isinstance(position.get("volume"), (int, float))
                    or not isfinite(position["volume"]) or position["volume"] <= 0):
                unavailable_position = f"Invalid stop-loss/take-profit on simulated {symbol} position; trading halted"
                market_reasons[symbol] = unavailable_position
                continue
            bid, ask, point = market["bid"], market["ask"], market["point"]
            direction = 1 if side == "BUY" else -1
            exit_quote = bid if direction == 1 else ask
            stop_hit = exit_quote <= position["stop"] if direction == 1 else exit_quote >= position["stop"]
            target_hit = exit_quote >= position["target"] if direction == 1 else exit_quote <= position["target"]
            if stop_hit or target_hit:
                instrument = market["instrument"]
                fill = exit_quote - direction * point * slippage_points
                gross = ((fill - position["entry"]) * direction / instrument.tick_size
                         * instrument.tick_value * position["volume"])
                net = gross - 2 * commission_per_lot_per_side * position["volume"]
                state["positions"].remove(position)
                state["closed_trades"].append({"symbol": symbol, "side": side, "volume": position["volume"],
                                                "entry": position["entry"], "exit": fill, "pnl": net, "time": timestamp})
                state["daily_pnl"][day] = state["daily_pnl"].get(day, 0.0) + net
                self._log_trade({"time": timestamp, "event": "CLOSE", "symbol": symbol, "side": side,
                                 "volume": position["volume"], "price": fill, "pnl": round(net, 2),
                                 "reason": "stop loss" if stop_hit else "take profit"})

        floating_pnl = 0.0
        for position in state["positions"]:
            symbol = position["symbol"]
            market = markets.get(symbol)
            reason = market_reasons.get(symbol, "No fresh market snapshot for open position")
            if reason:
                unavailable_position = unavailable_position or f"Cannot value open {symbol} position: {reason}"
                continue
            floating_pnl += self._position_pnl(position, market, commission_per_lot_per_side,
                                                slippage_points)

        realized_pnl = state["daily_pnl"].get(day, 0.0)
        start_floating = state["daily_start_floating"].get(day)
        if start_floating is None:
            unavailable_position = unavailable_position or "Cannot establish day-start floating-P/L baseline"
            start_floating = 0.0
        total_daily_pnl = realized_pnl + floating_pnl - start_floating
        marked_equity = start_equity + total_daily_pnl
        peak_equity = max(state["daily_peak_equity"][day], marked_equity)
        state["daily_peak_equity"][day] = peak_equity
        loss_amount = max(0.0, -total_daily_pnl)
        drawdown_amount = max(0.0, peak_equity - marked_equity)
        halt_reason = state["daily_halts"].get(day)
        if not halt_reason and total_daily_pnl <= -start_equity * daily_loss_fraction:
            halt_reason = (f"Daily loss limit reached: realized + floating P/L is {total_daily_pnl:.2f} "
                           f"({loss_amount / start_equity:.2%} of day-start equity)")
        if not halt_reason and drawdown_amount >= start_equity * max_daily_drawdown_fraction:
            halt_reason = (f"Maximum daily drawdown reached: drawdown is {drawdown_amount:.2f} "
                           f"({drawdown_amount / start_equity:.2%} of day-start equity)")
        if halt_reason:
            state["daily_halts"][day] = halt_reason

        statuses = {}
        entry_block = halt_reason or unavailable_position
        open_symbols = {position["symbol"] for position in state["positions"]}
        for symbol, signal in signals.items():
            if signal.direction == "HOLD":
                statuses[symbol] = signal.reason
                continue
            if entry_block:
                statuses[symbol] = entry_block
                continue
            market = markets.get(symbol)
            reason = market_reasons.get(symbol, "No fresh market snapshot available")
            if reason:
                statuses[symbol] = reason
                continue
            if symbol in open_symbols:
                statuses[symbol] = "Existing simulated position is open"
                continue
            if len(state["positions"]) >= max_open_trades:
                statuses[symbol] = f"Maximum open simulated trades reached ({max_open_trades})"
                continue
            if signal.direction not in ("BUY", "SELL") or not isfinite(signal.atr) or signal.atr <= 0:
                statuses[symbol] = "Invalid signal or ATR; stop-loss cannot be calculated"
                continue
            if not all(isfinite(value) and value > 0 for value in (stop_atr_multiple, reward_risk_ratio)):
                statuses[symbol] = "Invalid stop-loss or take-profit configuration"
                continue

            instrument = market["instrument"]
            bid, ask, point = market["bid"], market["ask"], market["point"]
            direction = 1 if signal.direction == "BUY" else -1
            stop_distance = signal.atr * stop_atr_multiple
            quote = ask if direction == 1 else bid
            entry = quote + direction * point * slippage_points
            stop = entry - direction * stop_distance
            target = entry + direction * stop_distance * reward_risk_ratio
            if (not isfinite(stop_distance) or not self._valid_levels(signal.direction, entry, stop, target)):
                statuses[symbol] = "Invalid stop-loss/take-profit levels; entry blocked"
                continue
            minimum_stop_distance = float(market.get("stops_level", 0)) * point
            if (not isfinite(minimum_stop_distance) or minimum_stop_distance < 0
                    or stop_distance < minimum_stop_distance
                    or stop_distance * reward_risk_ratio < minimum_stop_distance):
                statuses[symbol] = (f"Invalid stop-loss/take-profit distance; broker minimum is "
                                    f"{minimum_stop_distance:g} price units")
                continue
            estimated_cost = (2 * commission_per_lot_per_side
                              + 2 * slippage_points * point / instrument.tick_size * instrument.tick_value)
            volume = position_size(demo_equity, stop_distance, instrument, risk_fraction, estimated_cost)
            if volume <= 0:
                statuses[symbol] = (f"Risk-sized volume is below broker minimum ({instrument.volume_min}) "
                                    "or broker volume constraints are invalid")
                continue
            new_position = {"symbol": symbol, "side": signal.direction, "volume": volume,
                            "entry": entry, "stop": stop, "target": target, "opened": timestamp}
            entry_floating = self._position_pnl(new_position, market,
                                                commission_per_lot_per_side, slippage_points)
            projected_daily_pnl = realized_pnl + floating_pnl + entry_floating - start_floating
            projected_equity = start_equity + projected_daily_pnl
            projected_peak = max(state["daily_peak_equity"][day], projected_equity)
            projected_drawdown = projected_peak - projected_equity
            if projected_daily_pnl <= -start_equity * daily_loss_fraction:
                statuses[symbol] = "Entry blocked: projected spread/transaction costs breach daily loss limit"
                continue
            if projected_drawdown >= start_equity * max_daily_drawdown_fraction:
                statuses[symbol] = "Entry blocked: projected spread/transaction costs breach daily drawdown limit"
                continue

            state["positions"].append(new_position)
            open_symbols.add(symbol)
            self._log_trade({"time": timestamp, "event": "OPEN_SIMULATED", "symbol": symbol,
                             "side": signal.direction, "volume": volume, "price": entry,
                             "reason": signal.reason})
            statuses[symbol] = "Paper entry simulated"
            floating_pnl += entry_floating
            total_daily_pnl = realized_pnl + floating_pnl - start_floating
            marked_equity = start_equity + total_daily_pnl
            peak_equity = max(state["daily_peak_equity"][day], marked_equity)
            state["daily_peak_equity"][day] = peak_equity
            loss_amount = max(0.0, -total_daily_pnl)
            drawdown_amount = max(0.0, peak_equity - marked_equity)
            if not halt_reason and total_daily_pnl <= -start_equity * daily_loss_fraction:
                halt_reason = (f"Daily loss limit reached: realized + floating P/L is {total_daily_pnl:.2f} "
                               f"({loss_amount / start_equity:.2%} of day-start equity)")
            if not halt_reason and drawdown_amount >= start_equity * max_daily_drawdown_fraction:
                halt_reason = (f"Maximum daily drawdown reached: drawdown is {drawdown_amount:.2f} "
                               f"({drawdown_amount / start_equity:.2%} of day-start equity)")
            if halt_reason:
                state["daily_halts"][day] = halt_reason
                entry_block = halt_reason

        state["daily_status"][day] = {
            "updated_at": timestamp,
            "day_start_equity": start_equity,
            "marked_equity": marked_equity,
            "net_pnl_today": total_daily_pnl,
            "realized_pnl": realized_pnl,
            "floating_pnl": floating_pnl,
            "day_start_floating_pnl": start_floating,
            "daily_loss_fraction": loss_amount / start_equity,
            "daily_drawdown_fraction": drawdown_amount / start_equity,
            "blocked": bool(halt_reason or unavailable_position),
            "reason": halt_reason or unavailable_position or "Daily limits clear",
            "symbols": statuses,
        }
        self._save(state)
        return state["daily_status"][day]

    def snapshot(self) -> dict:
        return self._load()


def configure_logging(path: str) -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_path, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")