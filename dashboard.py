import sqlite3
import pandas as pd
import streamlit as st
import yfinance as yf
from datetime import datetime
from database import DB_NAME, get_account, get_open_trades, get_all_trades
import bot

st.set_page_config(
    page_title="Swing Paper Trading Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 15px;
        border-left: 5px solid #1E88E5;
    }
    .stMetric {
        background: rgba(255, 255, 255, 0.05);
        padding: 12px;
        border-radius: 8px;
        border: 1px solid rgba(128, 128, 128, 0.2);
    }
</style>
""", unsafe_allow_html=True)


def get_live_prices(symbols: list) -> dict:
    """Fetch current market price for symbols, fallback to None."""
    if not symbols:
        return {}
    prices = {}
    try:
        data = yf.download(
            tickers=" ".join(symbols),
            period="1d",
            interval="1m",
            group_by="ticker",
            threads=True,
            progress=False
        )
        for sym in symbols:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if sym in data.columns.levels[0]:
                        sym_df = data[sym].dropna(subset=["Close"])
                        if not sym_df.empty:
                            prices[sym] = float(sym_df["Close"].iloc[-1])
                else:
                    if not data.empty and "Close" in data.columns:
                        prices[sym] = float(data["Close"].dropna().iloc[-1])
            except Exception:
                pass
    except Exception:
        pass

    # Individual fallback if batch failed
    for sym in symbols:
        if sym not in prices:
            try:
                t = yf.Ticker(sym)
                hist = t.history(period="1d")
                if not hist.empty:
                    prices[sym] = float(hist["Close"].iloc[-1])
            except Exception:
                pass
    return prices


# Header
st.title("📈 Automated Swing Paper Trader")
st.caption("Live monitoring of Nifty 200 swing trading strategy (50/200 EMA + 20 EMA Pullback + RSI)")

# Sidebar Controls & Info
with st.sidebar:
    st.header("⚙️ Bot Controls")
    
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.rerun()

    if st.button("⚡ Run Trading Bot Scan", type="primary", use_container_width=True):
        with st.spinner("Scanning Nifty 200 & updating portfolio..."):
            try:
                bot.run()
                st.success("Bot scan and trade execution completed!")
                st.rerun()
            except Exception as e:
                st.error(f"Error running bot: {e}")

    st.markdown("---")
    st.subheader("Strategy Parameters")
    st.markdown("""
    - **Universe**: Nifty 200 (NSE)
    - **Max Open Positions**: 4
    - **Risk per Trade**: ₹2,000 (1%)
    - **Trend Filter**: Price > 50 EMA > 200 EMA
    - **RSI Range**: 45 to 62
    - **Entry**: Within 2.5% of 20 EMA
    - **Stop Loss**: 5-Day Low / 1.5x ATR
    - **Target**: 1:2 Risk to Reward
    - **Friction**: 0.15% per round-trip
    """)
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Fetch data from DB
acc = get_account()
all_trades = get_all_trades()
open_trades = [t for t in all_trades if t["status"] == "OPEN"]
closed_trades = [t for t in all_trades if t["status"] in ("CLOSED_TARGET", "CLOSED_SL")]

# Fetch live prices for open positions
open_symbols = [t["symbol"] for t in open_trades]
live_prices = get_live_prices(open_symbols)

# Calculate Open Positions Valuation
open_market_val = 0.0
open_cost_val = 0.0
unrealized_pnl = 0.0

for t in open_trades:
    sym = t["symbol"]
    qty = t["qty"]
    entry_p = t["entry_price"]
    curr_p = live_prices.get(sym, entry_p)
    open_market_val += curr_p * qty
    open_cost_val += entry_p * qty

unrealized_pnl = open_market_val - open_cost_val
current_equity = acc["cash"] + open_market_val
total_trades_count = len(all_trades)
closed_count = len(closed_trades)

# Calculate Win Rate
if closed_count > 0:
    winning_trades = sum(1 for t in closed_trades if (t["pnl"] is not None and t["pnl"] > 0))
    win_rate = (winning_trades / closed_count) * 100.0
    win_rate_str = f"{win_rate:.1f}% ({winning_trades}/{closed_count})"
else:
    win_rate_str = "0.0% (0/0)"

equity_delta = current_equity - acc["initial_capital"]
equity_delta_pct = (equity_delta / acc["initial_capital"]) * 100

# Top Metrics Row
st.subheader("📊 Portfolio Performance")
m1, m2, m3, m4, m5 = st.columns(5)

with m1:
    st.metric(
        label="Current Equity",
        value=f"₹{current_equity:,.2f}",
        delta=f"₹{equity_delta:+,.2f} ({equity_delta_pct:+.2f}%)"
    )

with m2:
    st.metric(
        label="Available Cash",
        value=f"₹{acc['cash']:,.2f}"
    )

with m3:
    st.metric(
        label="Realized P&L",
        value=f"₹{acc['realized_pnl']:,.2f}",
        delta=f"₹{acc['realized_pnl']:+,.2f}" if acc['realized_pnl'] != 0 else None
    )

with m4:
    st.metric(
        label="Win Rate %",
        value=win_rate_str
    )

with m5:
    st.metric(
        label="Total Trades",
        value=f"{total_trades_count}",
        delta=f"{len(open_trades)} Active"
    )

st.markdown("---")

# Active Positions Section
st.subheader(f"📌 Active Positions ({len(open_trades)} / 4)")

if open_trades:
    positions_data = []
    for t in open_trades:
        sym = t["symbol"]
        qty = t["qty"]
        entry_p = t["entry_price"]
        curr_p = live_prices.get(sym, entry_p)
        pos_pnl = (curr_p - entry_p) * qty
        pos_pnl_pct = ((curr_p - entry_p) / entry_p) * 100
        
        positions_data.append({
            "Trade ID": f"#{t['id']}",
            "Symbol": sym,
            "Entry Date": t["entry_date"],
            "Entry Price": f"₹{entry_p:,.2f}",
            "Qty": qty,
            "Current Price": f"₹{curr_p:,.2f}",
            "Current Stop-Loss": f"₹{t['stop_loss']:,.2f}",
            "Target": f"₹{t['target']:,.2f}",
            "Unrealized P&L": f"₹{pos_pnl:+,.2f} ({pos_pnl_pct:+.2f}%)",
            "Current Status": t["status"]
        })
        
    df_positions = pd.DataFrame(positions_data)
    st.dataframe(df_positions, use_container_width=True, hide_index=True)
else:
    st.info("No active positions at the moment. Run the bot scan when market is open or after daily close to identify new setups.")

st.markdown("---")

# Trade History Section
st.subheader(f"📜 Trade History ({len(closed_trades)} Closed)")

if closed_trades:
    history_data = []
    for t in closed_trades:
        pnl = t["pnl"] if t["pnl"] is not None else 0.0
        exit_p = t["exit_price"] if t["exit_price"] is not None else 0.0
        exit_d = t["exit_date"] if t["exit_date"] is not None else "-"
        
        history_data.append({
            "Trade ID": f"#{t['id']}",
            "Symbol": t["symbol"],
            "Entry Date": t["entry_date"],
            "Entry Price": f"₹{t['entry_price']:,.2f}",
            "Qty": t["qty"],
            "Exit Date": exit_d,
            "Exit Price": f"₹{exit_p:,.2f}",
            "Net P&L": f"₹{pnl:+,.2f}",
            "Exit Status": t["status"]
        })
        
    df_history = pd.DataFrame(history_data)
    st.dataframe(df_history, use_container_width=True, hide_index=True)
else:
    st.info("No closed trades recorded yet. Completed trades (Target or Stop Loss triggered) will be displayed here.")
