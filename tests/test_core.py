import unittest
import sys
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from moses_ai_trader.backtest import run_backtest
from moses_ai_trader.market_data import validate_market_data
from moses_ai_trader.mt5_adapter import demo_terminal
from moses_ai_trader.paper import PaperBook
from moses_ai_trader.risk import Instrument, position_size
from moses_ai_trader.strategy import Bar, Signal, analyze


class RiskTests(unittest.TestCase):
    def test_volume_stays_within_quarter_percent_risk(self):
        instrument = Instrument(tick_size=0.01, tick_value=1.0, volume_min=0.01, volume_max=100, volume_step=0.01)
        volume = position_size(10_000, 1.0, instrument, risk_fraction=0.5)
        self.assertEqual(volume, 0.25)
        self.assertLessEqual(volume * 1.0 / 0.01 * 1.0, 10_000 * 0.0025)

    def test_below_minimum_lot_returns_zero(self):
        instrument = Instrument(0.01, 1.0, 0.1, 100, 0.1)
        self.assertEqual(position_size(100, 10, instrument), 0.0)

    def test_estimated_transaction_costs_reduce_size(self):
        instrument = Instrument(0.01, 1.0, 0.01, 100, 0.01)
        no_cost = position_size(10_000, 1.0, instrument)
        with_cost = position_size(10_000, 1.0, instrument, estimated_cost_per_lot=100)
        self.assertLess(with_cost, no_cost)
        risk = (1.0 / instrument.tick_size * instrument.tick_value + 100) * with_cost
        self.assertLessEqual(risk, 10_000 * 0.0025)

    def test_volume_respects_nonzero_minimum_step_and_maximum(self):
        instrument = Instrument(0.01, 1.0, 0.1, 0.83, 0.25)
        volume = position_size(10_000, 0.1, instrument)
        self.assertEqual(volume, 0.6)
        self.assertGreaterEqual(volume, instrument.volume_min)
        self.assertLessEqual(volume, instrument.volume_max)
        self.assertAlmostEqual((volume - instrument.volume_min) % instrument.volume_step, 0.0)

    def test_invalid_volume_grid_returns_zero(self):
        instrument = Instrument(0.01, 1.0, 0.1, 0.05, 0.01)
        self.assertEqual(position_size(10_000, 0.1, instrument), 0.0)


class StrategyTests(unittest.TestCase):
    def test_requires_sufficient_closed_bars(self):
        signal = analyze([Bar("t", 1, 1, 1, 1)])
        self.assertEqual(signal.direction, "HOLD")
        self.assertIn("Need at least", signal.reason)

    def test_flat_market_does_not_create_signal(self):
        bars = [Bar(str(index), 1, 1.001, 0.999, 1) for index in range(70)]
        self.assertEqual(analyze(bars).direction, "HOLD")


class IntegrationSafetyTests(unittest.TestCase):
    def test_flat_backtest_has_no_phantom_trades(self):
        bars = [Bar(f"2026-01-01T00:{index:02d}:00", 1, 1.001, 0.999, 1)
                for index in range(80)]
        instrument = Instrument(0.00001, 1.0, 0.01, 100, 0.01)
        result = run_backtest(bars, instrument)
        self.assertEqual(result["trade_count"], 0)
        self.assertEqual(result["ending_equity"], result["starting_equity"])

    def test_terminal_integration_refuses_non_windows(self):
        with patch("moses_ai_trader.mt5_adapter.sys.platform", "darwin"):
            with self.assertRaisesRegex(RuntimeError, "Windows only"):
                with demo_terminal(["EURUSD"]):
                    self.fail("The terminal adapter must not initialize on macOS")

    def test_terminal_initialization_failure_is_reported(self):
        fake_terminal = SimpleNamespace(initialize=lambda: False,
                                        last_error=lambda: (-10004, "IPC connection failed"))
        with patch("moses_ai_trader.mt5_adapter.sys.platform", "win32"), \
                patch.dict(sys.modules, {"MetaTrader5": fake_terminal}):
            with self.assertRaisesRegex(RuntimeError, "MT5 initialize failed"):
                with demo_terminal(["EURUSD"]):
                    self.fail("A failed terminal initialization must not scan")

    def test_stale_quote_is_rejected(self):
        bars = [Bar("2026-01-01T00:00:00+00:00", 1, 1.01, 0.99, 1)]
        reason = validate_market_data(bars, 1.0, 1.0001, 0.00001, 900, now=1_000,
                                      max_quote_age_seconds=30)
        self.assertIn("Stale quote", reason)

    def test_excessive_spread_is_rejected(self):
        bars = [Bar("1970-01-01T00:16:40+00:00", 1, 1.01, 0.99, 1)]
        reason = validate_market_data(bars, 1.0, 1.001, 0.00001, 1_000, now=1_000,
                                      max_spread_points=50)
        self.assertIn("Spread too wide", reason)

    def test_invalid_quote_is_rejected(self):
        bars = [Bar("1970-01-01T00:16:40+00:00", 1, 1.01, 0.99, 1)]
        reason = validate_market_data(bars, 1.0, 1.0, 0.00001, 1_000, now=1_000)
        self.assertIn("Invalid bid, ask, point", reason)

    def test_malformed_ohlc_bar_is_rejected(self):
        bars = [Bar("1970-01-01T00:16:40+00:00", 1, 0.9, 0.95, 1)]
        reason = validate_market_data(bars, 1.0, 1.0001, 0.00001, 1_000, now=1_000)
        self.assertIn("invalid OHLC", reason)

    def test_stale_closed_bar_is_rejected(self):
        bars = [Bar("1970-01-01T00:00:00+00:00", 1, 1.01, 0.99, 1)]
        reason = validate_market_data(bars, 1.0, 1.0001, 0.00001, 10_000, now=10_000,
                                      max_bar_age_seconds=30)
        self.assertIn("Stale closed bar", reason)


class PaperRiskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.book = PaperBook(f"{self.temporary.name}/state.json", f"{self.temporary.name}/trades.csv", 10_000)
        self.now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        self.instrument = Instrument(0.01, 1.0, 0.01, 100, 0.01)
        self.markets = {"EURUSD": self.market()}
        self.buy = Signal("BUY", "test entry", 1.0, 2.0, 1.0, 60.0)

    def tearDown(self):
        self.temporary.cleanup()

    def market(self, bid=100.0, ask=100.01, instrument=None, point=0.01, stops_level=0, error=None):
        return {"bid": bid, "ask": ask, "point": point,
                "instrument": instrument or self.instrument if hasattr(self, "instrument") else instrument,
                "stops_level": stops_level, "market_error": error}

    def scan(self, markets=None, signals=None, *, daily_loss=0.02, drawdown=0.02,
             max_open=3, stop_multiple=1.5, now=None, max_spread=50, equity=10_000):
        return self.book.process_scan(
            markets if markets is not None else self.markets,
            signals if signals is not None else {"EURUSD": self.buy}, equity,
            daily_loss_fraction=daily_loss, max_daily_drawdown_fraction=drawdown,
            max_open_trades=max_open, stop_atr_multiple=stop_multiple,
            reward_risk_ratio=2.0, risk_fraction=0.0025,
            commission_per_lot_per_side=0.0, slippage_points=0.0,
            max_spread_points=max_spread, now=now or self.now,
        )

    def test_floating_loss_halts_entries_and_reports_reason(self):
        self.scan(daily_loss=0.0001, drawdown=0.5)
        markets = {"EURUSD": self.market(99.9, 99.91), "GBPUSD": self.market(100, 100.01)}
        status = self.scan(markets, {"EURUSD": self.buy, "GBPUSD": self.buy},
                           daily_loss=0.0001, drawdown=0.5)
        self.assertTrue(status["blocked"])
        self.assertIn("Daily loss limit reached", status["reason"])
        self.assertLess(status["floating_pnl"], 0)
        self.assertNotIn("GBPUSD", [position["symbol"] for position in self.book.snapshot()["positions"]])

    def test_realized_loss_halts_new_entries(self):
        self.scan(daily_loss=0.0001, drawdown=0.5)
        markets = {"EURUSD": self.market(98.5, 98.51), "GBPUSD": self.market(100, 100.01)}
        status = self.scan(markets, {"EURUSD": self.buy, "GBPUSD": self.buy},
                           daily_loss=0.0001, drawdown=0.5)
        self.assertTrue(status["blocked"])
        self.assertIn("Daily loss limit reached", status["reason"])
        self.assertLess(status["realized_pnl"], 0)
        self.assertEqual(len(self.book.snapshot()["positions"]), 0)

    def test_daily_drawdown_limit_uses_persisted_day_start_equity(self):
        self.scan(daily_loss=0.5, drawdown=0.0001, equity=10_000)
        later = datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)
        markets = {"EURUSD": self.market(99.9, 99.91)}
        status = self.scan(markets, {"EURUSD": self.buy}, daily_loss=0.5,
                           drawdown=0.0001, equity=20_000, now=later)
        self.assertIn("Maximum daily drawdown reached", status["reason"])
        self.assertEqual(status["day_start_equity"], 10_000)

    def test_carried_position_floating_pnl_is_baselined_at_day_start(self):
        self.scan()
        next_day = datetime(2026, 6, 2, 0, 5, tzinfo=timezone.utc)
        hold = Signal("HOLD", "no entry", 0, 0, 0, 50)
        status = self.scan(signals={"EURUSD": hold}, daily_loss=0.00001,
                           drawdown=0.00001, now=next_day)
        self.assertFalse(status["blocked"])
        self.assertAlmostEqual(status["net_pnl_today"], 0.0)
        self.assertAlmostEqual(status["floating_pnl"], status["day_start_floating_pnl"])

    def test_maximum_open_trades_blocks_additional_entries(self):
        markets = {"EURUSD": self.market(), "GBPUSD": self.market()}
        status = self.scan(markets, {"EURUSD": self.buy, "GBPUSD": self.buy}, max_open=1)
        self.assertIn("Maximum open simulated trades", status["symbols"]["GBPUSD"])
        self.assertEqual(len(self.book.snapshot()["positions"]), 1)

    def test_daily_halt_resets_on_next_utc_day(self):
        self.scan(daily_loss=0.0001, drawdown=0.5)
        markets = {"EURUSD": self.market(99.9, 99.91)}
        halted = self.scan(markets, {"EURUSD": self.buy}, daily_loss=0.0001, drawdown=0.5)
        self.assertIn("Daily loss limit reached", halted["reason"])
        next_day = self.now + timedelta(days=1)
        recovered = {"EURUSD": self.market(), "GBPUSD": self.market()}
        status = self.scan(recovered, {"GBPUSD": self.buy}, daily_loss=0.0001,
                           drawdown=0.5, now=next_day)
        self.assertFalse(status["blocked"])
        self.assertIn("GBPUSD", [position["symbol"] for position in self.book.snapshot()["positions"]])

    def test_invalid_daily_risk_config_fails_closed(self):
        status = self.scan(daily_loss=-0.01)
        self.assertTrue(status["blocked"])
        self.assertIn("Invalid risk-control configuration", status["reason"])
        self.assertEqual(self.book.snapshot()["positions"], [])

    def test_new_entry_costs_are_marked_in_same_scan(self):
        status = self.scan(daily_loss=0.00001, drawdown=0.5)
        self.assertIn("projected spread/transaction costs", status["symbols"]["EURUSD"])
        self.assertEqual(self.book.snapshot()["positions"], [])

    def test_stale_open_position_blocks_all_new_entries(self):
        self.scan()
        markets = {"EURUSD": self.market(error="Stale quote (900s old)"),
                   "GBPUSD": self.market(100, 100.01)}
        status = self.scan(markets, {"GBPUSD": self.buy})
        self.assertTrue(status["blocked"])
        self.assertIn("Cannot value open EURUSD position", status["reason"])
        self.assertEqual(len(self.book.snapshot()["positions"]), 1)

    def test_excessive_spread_blocks_simulated_entry(self):
        status = self.scan({"EURUSD": self.market(100, 100.1)}, max_spread=5)
        self.assertIn("Spread too wide", status["symbols"]["EURUSD"])
        self.assertEqual(self.book.snapshot()["positions"], [])

    def test_invalid_stop_loss_and_broker_stop_distance_block_entry(self):
        status = self.scan(stop_multiple=0)
        self.assertIn("Invalid stop-loss", status["symbols"]["EURUSD"])
        self.assertEqual(self.book.snapshot()["positions"], [])
        status = self.scan({"EURUSD": self.market(stops_level=200)})
        self.assertIn("broker minimum", status["symbols"]["EURUSD"])
        self.assertEqual(self.book.snapshot()["positions"], [])

    def test_volume_below_broker_minimum_blocks_entry(self):
        instrument = Instrument(0.01, 1.0, 1.0, 10, 1.0)
        status = self.scan({"EURUSD": self.market(instrument=instrument)})
        self.assertIn("below broker minimum", status["symbols"]["EURUSD"])
        self.assertEqual(self.book.snapshot()["positions"], [])

    def test_daily_halt_is_latched_for_remainder_of_day(self):
        self.scan(daily_loss=0.0001, drawdown=0.5)
        markets = {"EURUSD": self.market(99.9, 99.91), "GBPUSD": self.market(100, 100.01)}
        self.scan(markets, {"EURUSD": self.buy, "GBPUSD": self.buy}, daily_loss=0.0001, drawdown=0.5)
        recovered = {"EURUSD": self.market(), "GBPUSD": self.market()}
        status = self.scan(recovered, {"GBPUSD": self.buy}, daily_loss=0.0001, drawdown=0.5)
        self.assertIn("Daily loss limit reached", status["reason"])
        self.assertNotIn("GBPUSD", [position["symbol"] for position in self.book.snapshot()["positions"]])


if __name__ == "__main__":
    unittest.main()