import logging
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from moses_ai_trader.mt5_adapter import demo_terminal
from moses_ai_trader.paper import PaperBook, configure_logging
from moses_ai_trader.strategy import Signal, analyze


ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
RISK = CONFIG["risk"]
PAPER = CONFIG["paper"]
book = PaperBook(str(ROOT / PAPER["state_path"]), str(ROOT / PAPER["trade_log_path"]), PAPER["initial_equity"])
configure_logging(str(ROOT / PAPER["error_log_path"]))

st.set_page_config(page_title="Moses AI Trader", layout="wide")
st.title("Moses AI Trader")
st.caption("DEMO / DRY-RUN only. All positions shown here are simulated.")

if st.button("Scan demo terminal", type="primary"):
    try:
        market_config = CONFIG["market_data"]
        with demo_terminal(
            CONFIG["app"]["symbols"], CONFIG["app"]["bars"], CONFIG["app"]["timeframe"],
            max_quote_age_seconds=market_config["max_quote_age_seconds"],
            max_bar_age_seconds=market_config["max_closed_bar_age_seconds"],
            max_spread_points=market_config["max_spread_points"],
        ) as (account, snapshot):
            st.session_state.account = {"equity": float(account.equity), "currency": account.currency,
                                        "login": str(account.login)}
            signals = {}
            rows = []
            for symbol in CONFIG["app"]["symbols"]:
                data = snapshot.get(symbol, {"market_error": "No market snapshot returned"})
                error = data.get("market_error")
                signal = (Signal("HOLD", error, 0, 0, 0, 50) if error
                          else analyze(data["bars"], **CONFIG["strategy"]))
                signals[symbol] = signal
                rows.append({"Symbol": symbol, "Signal": signal.direction, "Signal detail": signal.reason,
                             "RSI": round(signal.rsi, 2), "Fast MA": signal.fast_ma,
                             "Slow MA": signal.slow_ma, "ATR": signal.atr,
                             "Spread": round(data["ask"] - data["bid"], 6) if not error else "Unavailable"})
            daily_risk = book.process_scan(
                snapshot, signals, float(account.equity),
                daily_loss_fraction=RISK["daily_loss_fraction"],
                max_daily_drawdown_fraction=RISK["max_daily_drawdown_fraction"],
                max_open_trades=RISK["max_open_trades"],
                stop_atr_multiple=RISK["stop_atr_multiple"],
                reward_risk_ratio=RISK["reward_risk_ratio"],
                risk_fraction=RISK["max_trade_risk_fraction"],
                commission_per_lot_per_side=PAPER["commission_per_lot_per_side"],
                slippage_points=PAPER["slippage_points"],
                max_spread_points=market_config["max_spread_points"],
            )
            for row in rows:
                row["Entry status"] = daily_risk["symbols"].get(row["Symbol"], "No entry evaluated")
            st.session_state.signals = rows
            st.session_state.daily_risk = daily_risk
            st.session_state.connection = "Connected to DEMO terminal"
    except Exception as error:
        logging.exception("Demo scan failed")
        st.session_state.pop("account", None)
        st.session_state.pop("signals", None)
        st.session_state.connection = f"Unavailable: {error}"

account = st.session_state.get("account", {})
st.write(f"**Connection:** {st.session_state.get('connection', 'Not connected; scan to connect')}")
if st.session_state.get("connection", "").startswith("Unavailable:"):
    st.error(f"Scan failed; no new simulated trades were evaluated. {st.session_state.connection}")
if account:
    left, right = st.columns(2)
    left.metric("Demo account equity", f"{account['equity']:,.2f} {account['currency']}")
    right.metric("Account", f"DEMO · {account['login']}")

if "signals" in st.session_state:
    st.subheader(f"Latest closed-bar signals · {CONFIG['app']['timeframe']}")
    st.dataframe(st.session_state.signals, use_container_width=True, hide_index=True)

state = book.snapshot()
today = datetime.now(timezone.utc).date().isoformat()
daily_risk = state.get("daily_status", {}).get(today)
if daily_risk:
    st.subheader(f"Daily risk controls · last scan {daily_risk.get('updated_at', 'unknown')}")
    if daily_risk["blocked"]:
        st.error(f"New simulated trades blocked: {daily_risk['reason']}")
    else:
        st.success("Daily loss and drawdown limits clear")
    risk_columns = st.columns(6)
    risk_columns[0].metric("Day-start equity", f"{daily_risk.get('day_start_equity', 0):,.2f}")
    risk_columns[1].metric("Marked equity", f"{daily_risk.get('marked_equity', 0):,.2f}")
    risk_columns[2].metric("Net P/L today", f"{daily_risk.get('net_pnl_today', 0):,.2f}")
    risk_columns[3].metric("Closed trade P/L", f"{daily_risk.get('realized_pnl', 0):,.2f}")
    risk_columns[4].metric("Floating P/L", f"{daily_risk.get('floating_pnl', 0):,.2f}")
    risk_columns[5].metric("Loss / drawdown", f"{daily_risk.get('daily_loss_fraction', 0):.2%} / "
                           f"{daily_risk.get('daily_drawdown_fraction', 0):.2%}")
closed = state["closed_trades"]
realized = sum(trade["pnl"] for trade in closed)
wins = sum(trade["pnl"] > 0 for trade in closed)
st.subheader("Paper performance")
columns = st.columns(4)
columns[0].metric("Paper balance (realized)", f"{PAPER['initial_equity'] + realized:,.2f}")
columns[1].metric("Realized P/L", f"{realized:,.2f}")
columns[2].metric("Closed trades", str(len(closed)))
columns[3].metric("Win rate", f"{wins / len(closed):.1%}" if closed else "—")
st.subheader("Simulated open trades")
st.dataframe(state["positions"], use_container_width=True, hide_index=True)
st.subheader("Closed simulated trades")
st.dataframe(list(reversed(closed[-100:])), use_container_width=True, hide_index=True)