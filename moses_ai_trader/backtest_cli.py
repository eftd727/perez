import argparse
import csv
import tomllib
from pathlib import Path

from moses_ai_trader.backtest import BacktestConfig, load_bars_csv, run_backtest
from moses_ai_trader.risk import Instrument


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the M15 strategy on an OHLC CSV file.")
    parser.add_argument("csv", help="CSV with time,open,high,low,close columns")
    parser.add_argument("--tick-size", type=float, required=True, help="Broker symbol trade tick size")
    parser.add_argument("--tick-value", type=float, required=True, help="Tick value in account currency per lot")
    parser.add_argument("--volume-min", type=float, required=True)
    parser.add_argument("--volume-max", type=float, required=True)
    parser.add_argument("--volume-step", type=float, required=True)
    parser.add_argument("--spread-price", type=float, required=True, help="Assumed full spread in price units")
    parser.add_argument("--slippage-price", type=float, default=0.00002)
    parser.add_argument("--commission-per-lot-per-side", type=float, default=3.5)
    parser.add_argument("--starting-equity", type=float, default=10_000)
    parser.add_argument("--daily-loss-fraction", type=float, default=0.02)
    parser.add_argument("--output", default="results/backtest_trades.csv")
    args = parser.parse_args()

    project_config = tomllib.loads((Path(__file__).resolve().parent.parent / "config.toml").read_text(encoding="utf-8"))
    risk = project_config["risk"]
    config = BacktestConfig(starting_equity=args.starting_equity,
                            risk_fraction=risk["max_trade_risk_fraction"],
                            stop_atr_multiple=risk["stop_atr_multiple"],
                            reward_risk_ratio=risk["reward_risk_ratio"],
                            daily_loss_fraction=args.daily_loss_fraction,
                            spread_price=args.spread_price,
                            slippage_price=args.slippage_price,
                            commission_per_lot_per_side=args.commission_per_lot_per_side)
    result = run_backtest(load_bars_csv(args.csv), Instrument(
        args.tick_size, args.tick_value, args.volume_min, args.volume_max, args.volume_step), config,
        project_config["strategy"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["side", "entry_time", "exit_time", "volume", "net_pnl", "equity"]
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result["trades"])
    print(f"Trades: {result['trade_count']}")
    print(f"Net P/L: {result['net_pnl']:.2f}")
    print(f"Ending equity: {result['ending_equity']:.2f}")
    print(f"Win rate: {result['win_rate']:.1%}")
    print(f"Maximum drawdown: {result['max_drawdown_fraction']:.1%}")
    print(f"Trade ledger: {output}")
    print("Historical simulation only; this is not evidence of future profitability.")


if __name__ == "__main__":
    main()