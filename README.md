# Moses AI Trader

Moses AI Trader is a personal MetaTrader 5 market scanner with a simulated trade book and a historical CSV backtester. Its only operating mode is DEMO / DRY-RUN: the project contains no order-placement call and cannot submit a trade. It refuses to read market data unless the connected MT5 account is a demo account.

## Platform support

This development computer is macOS. The official `MetaTrader5` Python package communicates with the Windows MT5 terminal and does not run its terminal integration on macOS. Strategy analysis, risk sizing, the CSV backtester, and unit tests are portable; live terminal scanning and the dashboard's account-equity view require Windows with the MT5 terminal installed and running. Use a Windows PC or Windows VPS for that part.

The current host has Python 3.15. Use 64-bit Python 3.11 or 3.12 for installation, especially on Windows where the MT5 Python package must match the installed Python architecture. Do not put a broker password in this project or in chat. Sign into a **demo account** in the MT5 terminal itself; the adapter uses the terminal's existing session and does not accept login credentials.

## Windows setup

1. Install 64-bit Python 3.11 or 3.12 and MetaTrader 5 from your broker. Install the MT5 terminal on the same Windows computer as Python.
2. Start the terminal and sign in to a broker **demo** account. Confirm the terminal is connected and the account is demo. Never use a live account with this project.
3. Open PowerShell in this project folder and create/activate a virtual environment:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. Edit `config.toml` to match the broker's exact symbol names. Some brokers use names such as `EURUSD.a` or `XAUUSDm`. Defaults scan EURUSD, GBPUSD, USDJPY, and XAUUSD on M15.
5. Start the dashboard:

   ```powershell
   python -m streamlit run moses_ai_trader/dashboard.py
   ```

6. In the browser, choose **Scan demo terminal**. Each scan reads closed bars and current bid/ask quotes; it never submits or modifies an MT5 order. Simulated state is stored in `data/paper_state.json`; trade events and errors are written under `logs/`.

## macOS development and tests

The portable parts can be tested on macOS with Python 3.11/3.12:

```shell
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The Streamlit dashboard can launch, but terminal scans will report that the official MT5 integration is Windows-only. No account connection is attempted on macOS.

## Strategy and safeguards

The initial M15 entry rule combines a 20/50 simple moving-average trend, RSI(14) ranges, and a close beyond the prior candle's high/low. The stop distance is 1.5 ATR(14), and the target is 2 times the stop distance. These and the scanned symbols are configurable in `config.toml`.

Position size is rounded down to a valid broker volume-step increment without exceeding the broker maximum. The requested risk fraction is hard-capped in code at 0.25% of the latest demo equity per trade, with estimated round-trip commission and slippage reserved before sizing. A position below the broker minimum is rejected rather than rounded up. Broker stop-distance requirements are checked before a simulated entry. The maximum open paper positions defaults to three.

The default daily loss and maximum daily drawdown limits are each 2% of day-start equity. Daily loss includes closed-trade P/L plus current floating P/L movement from the day's starting mark; carried positions are baselined on the first reliable scan of each UTC day. Drawdown is measured from the day's marked-equity high-water mark and normalized against day-start equity. Once either limit is reached, new simulated entries stay blocked until the next UTC day, with the reason displayed in the dashboard. A stale/missing quote for an open position blocks all new entries because portfolio risk cannot be valued safely. A connection failure never runs the paper-entry scan.

Quotes older than 300 seconds, closed bars older than 2,700 seconds, and spreads above 50 broker points are rejected by default. Adjust these limits in `config.toml` to fit the broker and instrument; XAUUSD and less-liquid symbols may need different spread limits. Invalid OHLC/quote values and invalid stop/take-profit levels are also blocked.

Paper entries use current bid/ask and configured slippage. The backtester applies an assumed spread and slippage on entry and exit and commission on both sides. Backtest OHLC bars are treated as reference/mid prices; set cost assumptions for each symbol (especially XAUUSD) and check your broker's tick size, tick value, lot limits, and account currency. The simple bar-based simulator does not model swaps, gaps beyond its stop fill, latency, partial fills, or changing spreads. An open position at the final bar is reported but not force-closed.

## Historical backtest

Export at least 60 timestamped M15 bars to CSV with these case-sensitive columns: `time,open,high,low,close`. Provide broker-specific tick/volume metadata and a full spread estimate in price units:

```powershell
python -m moses_ai_trader.backtest_cli history.csv `
  --tick-size 0.00001 --tick-value 1.00 `
  --volume-min 0.01 --volume-max 100 --volume-step 0.01 `
  --spread-price 0.00012 --slippage-price 0.00002 `
  --commission-per-lot-per-side 3.50 --output results/eurusd.csv
```

Use symbol-specific values; the example is illustrative and not suitable for every broker or instrument. The command prints trade count, net P/L, win rate, and maximum drawdown and writes a trade ledger. Review results across multiple market regimes, include out-of-sample data, and forward-test on demo before drawing conclusions. Backtesting and demo performance do not establish profitability or predict future results.

## Configuration and data

- `config.toml`: symbols, M15 strategy parameters, risk controls, costs, and local file paths.
- `data/paper_state.json`: local simulated positions and closed trades. Back it up before moving machines; deleting it resets paper history.
- `logs/trades.csv`: simulated opens and closes.
- `logs/moses.log`: connection and scan errors.

Do not enable live trading: this project intentionally has no live-trading mode.