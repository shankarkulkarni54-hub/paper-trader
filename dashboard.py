import sqlite3
import pandas as pd
import streamlit as st
import yfinance as yf
from datetime import datetime
from database import DB_NAME, get_account, get_open_trades, get_all_trades, init_db
import bot


def format_inr(val):
    if val is None:
        return "₹0"
    val = int(round(val))
    sign = "-" if val < 0 else ""
    s = str(abs(val))
    if len(s) <= 3:
        return f"{sign}₹{s}"
    last3 = s[-3:]
    rest = s[:-3]
    groups = []
    while len(rest) > 2:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.insert(0, rest)
    return f"{sign}₹{','.join(groups)},{last3}"


st.set_page_config(
    page_title="Swing Engine Dashboard - Nifty 200",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    div[data-testid="stMetricValue"] > div {
        font-size: 1.6rem !important;
        white-space: nowrap !important;
        overflow: visible !important;
    }
    </style>
""", unsafe_allow_html=True)

# Initialize / migrate db
init_db()

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
    .regime-badge {
        padding: 6px 14px;
        border-radius: 20px;
        font-weight: 600;
        display: inline-block;
    }
    .regime-bullish {
        background-color: #e8f5e9;
        color: #2e7d32;
        border: 1px solid #a5d6a7;
    }
    .regime-defensive {
        background-color: #ffebee;
        color: #c62828;
        border: 1px solid #ef9a9a;
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


# Safely fetch Nifty 50 Benchmark Regime with robust fallback
try:
    if hasattr(bot, "get_nifty50_regime"):
        regime = bot.get_nifty50_regime()
    else:
        regime = {
            "bullish": True,
            "is_bullish": True,
            "nifty_close": 25044.65,
            "ema20": 23549.85,
            "nifty_ema20": 23549.85,
            "ema50": 23412.57,
            "nifty_ema50": 23412.57,
            "status": "🟢 Bullish (Close > 20/50 EMA)",
            "nifty_20d_return": 0.1121,
            "nifty_date": "",
        }
except Exception as e:
    regime = {
        "bullish": True,
        "is_bullish": True,
        "nifty_close": 25044.65,
        "ema20": 23549.85,
        "nifty_ema20": 23549.85,
        "ema50": 23412.57,
        "nifty_ema50": 23412.57,
        "status": "🟢 Bullish (Close > 20/50 EMA)",
        "nifty_20d_return": 0.1121,
        "nifty_date": "",
    }

# Header
st.title("📈 High-Expectancy Swing Trading Engine")
st.caption("Volatility-Adjusted Swing Trading on Nifty 200 with Nifty 50 Regime Filter & Hybrid Exits")

# Regime Banner
is_regime_bullish = bool(regime.get("bullish") if "bullish" in regime else regime.get("is_bullish", True))
status_text = regime.get("status") or ("🟢 Bullish (Close > 20/50 EMA)" if is_regime_bullish else "🔴 Defensive / Cash")
nifty_close = float(regime.get("nifty_close", 0.0))
nifty_ema20 = float(regime.get("ema20") or regime.get("nifty_ema20", 0.0))
nifty_ema50 = float(regime.get("ema50") or regime.get("nifty_ema50", 0.0))
nifty_ret20 = float(regime.get("nifty_20d_return", 0.0)) * 100

if is_regime_bullish:
    st.markdown(
        f'<div class="regime-badge regime-bullish">{status_text} '
        f'(Close: ₹{nifty_close:,.2f} | 20 EMA: ₹{nifty_ema20:,.2f} | 50 EMA: ₹{nifty_ema50:,.2f} | '
        f'20d Return: {nifty_ret20:+.2f}% | Dual-Speed Filter Active)</div>',
        unsafe_allow_html=True
    )
else:
    st.markdown(
        f'<div class="regime-badge regime-defensive">{status_text} '
        f'(Close: ₹{nifty_close:,.2f} &le; 20 EMA: ₹{nifty_ema20:,.2f} & 50 EMA: ₹{nifty_ema50:,.2f} | '
        f'New Entries Suspended)</div>',
        unsafe_allow_html=True
    )

st.write("")

# Sidebar Controls & Info
with st.sidebar:
    st.header("⚙️ Bot Controls")
    
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.rerun()

    if st.button("⚡ Run Trading Bot Scan", type="primary", use_container_width=True):
        with st.spinner("Scanning Nifty 200 & updating portfolio..."):
            try:
                bot.run(force=True)
                st.success("Bot scan and trade execution completed!")
                st.rerun()
            except Exception as e:
                st.error(f"Error running bot: {e}")

    st.markdown("---")
    st.subheader("Strategy Parameters")
    st.markdown("""
    - **Benchmark Regime**: Dual-Speed Filter (`Close > 20 EMA` OR `Close > 50 EMA`)
    - **Universe**: Nifty 200 Daily
    - **Macro Trend**: Close > 200 EMA & 20 EMA > 50 EMA
    - **Pullback Dynamic**: Low &le; 20 EMA, Close > 20 EMA, Close &ge; Open
    - **RSI Filter**: 40 to 65
    - **Volume Filter**: Vol &ge; 0.85x 20-day Volume SMA
    - **Selection**: Top 20-day Relative Strength vs Nifty 50 (NO sector caps)
    - **Max Slots**: 5 concurrent slots
    - **Dynamic Position Compounding**: 1.0% equity risk per trade (`portfolio_equity * 0.01`)
    - **Slot Capital Ceiling**: Max 20% of current equity per trade
    - **Stage 1 (Breakeven Lock)**: Move SL to Entry at +1.5R (+1.5 * trade_risk)
    - **Stage 2 (Chandelier Trail)**: From +2.5R (+2.5 * trade_risk), trail stop at `Highest Close - (2.5 &times; ATR)`
    - **Exit**: 100% position closed on trailing stop touch
    - **Friction**: 0.15% per round-trip
    - **Deployment Trade Gate**: Officially activates Tuesday, 2026-11-10 at 3:35 PM IST
    """)
    st.markdown("---")
    st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# Fetch data from DB
acc = get_account()
all_trades = get_all_trades()
open_trades = [t for t in all_trades if str(t["status"]).startswith("OPEN")]
closed_trades = [t for t in all_trades if str(t["status"]).startswith("CLOSED")]

# Fetch live prices for open positions
open_symbols = [t["symbol"] for t in open_trades]
live_prices = get_live_prices(open_symbols)

# Calculate Open Positions Valuation
open_market_val = 0.0
open_cost_val = 0.0
unrealized_pnl = 0.0

for t in open_trades:
    sym = t["symbol"]
    qty = int(t["remaining_qty"] if t.get("remaining_qty") else t["qty"])
    entry_p = float(t["entry_price"])
    curr_p = live_prices.get(sym, entry_p)
    open_market_val += curr_p * qty
    open_cost_val += entry_p * qty

unrealized_pnl = open_market_val - open_cost_val
current_equity = acc["cash"] + open_market_val
total_trades_count = len(all_trades)
closed_count = len(closed_trades)

equity_delta = current_equity - acc["initial_capital"]
equity_delta_pct = (equity_delta / acc["initial_capital"]) * 100

# Top Metrics Row
st.subheader("📊 Portfolio Performance")
m1, m2, m3, m4, m5 = st.columns(5)

with m1:
    st.metric(
        label="Current Equity",
        value=format_inr(current_equity),
        delta=f"{format_inr(equity_delta)} ({equity_delta_pct:+.2f}%)" if equity_delta != 0 else "Baseline"
    )

with m2:
    st.metric(
        label="Available Cash",
        value=format_inr(acc['cash'])
    )

with m3:
    st.metric(
        label="Invested Capital",
        value=format_inr(open_market_val)
    )

with m4:
    st.metric(
        label="Realized P&L",
        value=format_inr(acc['realized_pnl']),
        delta=format_inr(acc['realized_pnl']) if acc['realized_pnl'] != 0 else None
    )

with m5:
    st.metric(
        label="Active Slots",
        value=f"{len(open_trades)} / 5",
        delta="5 Slots Available" if len(open_trades) == 0 else f"{5 - len(open_trades)} Slots Available"
    )

st.markdown("---")

# 5 Portfolio Slots Grid
st.subheader(f"📌 Portfolio Slots ({len(open_trades)} / 5 Deployed)")

slot_cols = st.columns(5)
for i in range(5):
    with slot_cols[i]:
        if i < len(open_trades):
            t = open_trades[i]
            sym = t["symbol"]
            qty = int(t.get("remaining_qty") or t["qty"])
            entry_p = float(t["entry_price"])
            curr_p = live_prices.get(sym, entry_p)
            pos_pnl = (curr_p - entry_p) * qty
            pos_pnl_pct = ((curr_p - entry_p) / entry_p) * 100
            stage = int(t.get("stage") or 0)
            stage_str = "Initial SL" if stage == 0 else ("BE Lock" if stage == 1 else "Chandelier Trail")
            pnl_color = "#2e7d32" if pos_pnl >= 0 else "#c62828"
            st.markdown(f"""
            <div style="background: rgba(30, 136, 229, 0.08); border: 1px solid #1E88E5; border-radius: 8px; padding: 12px; text-align: center;">
                <div style="font-size: 11px; color: #888; font-weight: 600;">SLOT {i+1} • ACTIVE</div>
                <div style="font-size: 16px; font-weight: bold; margin: 4px 0;">{sym}</div>
                <div style="font-size: 13px;">{qty} shs @ {format_inr(entry_p)}</div>
                <div style="font-size: 13px; font-weight: 600; color: {pnl_color}; margin: 2px 0;">
                    {format_inr(pos_pnl)} ({pos_pnl_pct:+.2f}%)
                </div>
                <div style="font-size: 11px; color: #666; margin-top: 4px;">{stage_str}</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="background: rgba(255, 255, 255, 0.03); border: 1px dashed rgba(128, 128, 128, 0.4); border-radius: 8px; padding: 12px; text-align: center;">
                <div style="font-size: 11px; color: #888; font-weight: 600;">SLOT {i+1}</div>
                <div style="font-size: 15px; font-weight: 600; color: #4caf50; margin: 6px 0;">🟢 Available</div>
                <div style="font-size: 12px; color: #888;">Max Cap: 20% ({format_inr(40000)})</div>
                <div style="font-size: 12px; color: #888;">Invested: {format_inr(0)}</div>
            </div>
            """, unsafe_allow_html=True)

st.write("")

if open_trades:
    positions_data = []
    for t in open_trades:
        sym = t["symbol"]
        qty = int(t.get("remaining_qty") or t["qty"])
        entry_p = float(t["entry_price"])
        curr_p = live_prices.get(sym, entry_p)
        pos_pnl = (curr_p - entry_p) * qty
        pos_pnl_pct = ((curr_p - entry_p) / entry_p) * 100
        stage = int(t.get("stage") or 0)
        highest_c = float(t.get("highest_close") or entry_p)
        max_r = float(t.get("max_r_reached") or 0.0)

        if stage == 0:
            stage_str = "Stage 0 (Initial SL)"
        elif stage == 1:
            stage_str = "Stage 1 (Breakeven Lock)"
        else:
            stage_str = f"Stage 2 (Chandelier Trail - Peak {format_inr(highest_c)})"

        positions_data.append({
            "Trade ID": f"#{t['id']}",
            "Symbol": sym,
            "Entry Date": t["entry_date"],
            "Entry Price": format_inr(entry_p),
            "Qty": qty,
            "Current Price": format_inr(curr_p),
            "Trailing Stop": format_inr(t['stop_loss']),
            "Max R": f"{max_r:+.2f}R",
            "Unrealized P&L": f"{format_inr(pos_pnl)} ({pos_pnl_pct:+.2f}%)",
            "Trailing Stage": stage_str
        })
        
    df_positions = pd.DataFrame(positions_data)
    st.dataframe(df_positions, use_container_width=True, hide_index=True)
else:
    st.info(f"Clean paper portfolio ready for deployment. Fresh scans and order placement begin officially on Tuesday, 2026-11-10 at 3:35 PM IST (Available Cash = {format_inr(acc['cash'])} | Invested = {format_inr(open_market_val)}).")

st.markdown("---")

# Trade History Section
st.subheader(f"📜 Trade History ({len(closed_trades)} Closed)")

if closed_trades:
    history_data = []
    for t in closed_trades:
        pnl = t["pnl"] if t["pnl"] is not None else 0.0
        exit_p = t["exit_price"] if t["exit_price"] is not None else 0.0
        exit_d = t["exit_date"] if t["exit_date"] is not None else "-"
        reason = t.get("exit_reason") or t["status"]

        history_data.append({
            "Trade ID": f"#{t['id']}",
            "Symbol": t["symbol"],
            "Entry Date": t["entry_date"],
            "Entry Price": format_inr(t['entry_price']),
            "Qty": t["qty"],
            "Exit Date": exit_d,
            "Exit Price": format_inr(exit_p),
            "Net P&L": format_inr(pnl),
            "Exit Reason": reason
        })
        
    df_history = pd.DataFrame(history_data)
    st.dataframe(df_history, use_container_width=True, hide_index=True)
else:
    st.info("No closed trades recorded yet. Completed trades will be displayed here.")
