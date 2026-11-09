import datetime
import math
import os
import sys
import time
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import yfinance as yf
import ta

from bot import load_nifty200_universe

# Production Configuration (5-slot ATR Chandelier Strategy)
INITIAL_CAPITAL = 200000.0
MAX_CONCURRENT_POSITIONS = 5                     # 5 concurrent slots
MAX_CAPITAL_PER_SLOT = INITIAL_CAPITAL / MAX_CONCURRENT_POSITIONS  # Rs 40,000 per slot
RISK_PER_TRADE = 2000.0                          # Rs 2,000 initial risk (1.5x ATR stop)
FRICTION_RATE = 0.0015                           # 0.15% round-trip friction / brokerage

DATA_START = "2015-01-01"                        # Historical data start for 10Y+ indicator warmup
DATA_END = "2026-11-10"

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_SYNC_FLAG = os.path.join(CACHE_DIR, ".multi_period_10y_synced")

# Output CSV paths
RESULTS_3Y_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_3y.csv")
RESULTS_5Y_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_5y.csv")
RESULTS_10Y_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_10y.csv")

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_cache_path(symbol: str) -> str:
    """Return local CSV path for a given symbol in .cache/."""
    safe_name = symbol.replace("^", "").strip()
    return os.path.join(CACHE_DIR, f"{safe_name}.csv")


def load_or_fetch_all_data(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    """
    Ensure all symbols have full historical data (from 2015) in .cache/.
    Downloads missing or incomplete historical data in batches and saves to CSV.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    loaded_data = {}
    missing_symbols = []

    print("[DATA] Checking local cache in .cache/ for 10-year historical data...")
    cache_synced = os.path.exists(CACHE_SYNC_FLAG)

    for sym in symbols:
        cache_file = get_cache_path(sym)
        if os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file)
                if not df.empty and "Close" in df.columns and len(df) >= 100:
                    date_col = "Date" if "Date" in df.columns else ("Datetime" if "Datetime" in df.columns else df.columns[0])
                    first_date = str(df[date_col].iloc[0])[:10]
                    # If synced flag exists or data starts on/before 2016-01-01, accept
                    if cache_synced or first_date <= "2016-01-01" or len(df) >= 2000:
                        df["Date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None)
                        df = df.sort_values("Date").reset_index(drop=True)
                        loaded_data[sym] = df
                    else:
                        missing_symbols.append(sym)
                else:
                    missing_symbols.append(sym)
            except Exception:
                missing_symbols.append(sym)
        else:
            missing_symbols.append(sym)

    print(f"[DATA] {len(loaded_data)} symbols fully cached. {len(missing_symbols)} require historical download.")

    if missing_symbols:
        print(f"[DATA] Downloading {len(missing_symbols)} symbols from yfinance ({DATA_START} to {DATA_END})...")
        batch_size = 50
        for i in range(0, len(missing_symbols), batch_size):
            batch = missing_symbols[i:i + batch_size]
            print(f"       Downloading batch {i // batch_size + 1}/{(len(missing_symbols) + batch_size - 1) // batch_size} ({len(batch)} tickers)...")
            try:
                data = yf.download(
                    tickers=batch,
                    start=DATA_START,
                    end=DATA_END,
                    interval="1d",
                    group_by="ticker",
                    threads=True,
                    progress=False
                )

                if isinstance(data.columns, pd.MultiIndex):
                    has_level0 = any(s in data.columns.levels[0] for s in batch)
                    for sym in batch:
                        try:
                            if has_level0 and sym in data.columns.levels[0]:
                                df_sym = data[sym].dropna(subset=["Close"]).copy()
                            elif not has_level0 and sym in data.columns.levels[1]:
                                df_sym = data.xs(sym, axis=1, level=1).dropna(subset=["Close"]).copy()
                            else:
                                continue

                            if not df_sym.empty and len(df_sym) >= 50:
                                df_sym = df_sym.reset_index()
                                date_col = "Date" if "Date" in df_sym.columns else ("Datetime" if "Datetime" in df_sym.columns else df_sym.columns[0])
                                df_sym["Date"] = pd.to_datetime(df_sym[date_col]).dt.tz_localize(None)
                                df_sym = df_sym.sort_values("Date").reset_index(drop=True)

                                df_sym.to_csv(get_cache_path(sym), index=False)
                                loaded_data[sym] = df_sym
                        except Exception:
                            pass
                else:
                    if len(batch) == 1:
                        sym = batch[0]
                        df_sym = data.dropna(subset=["Close"]).copy()
                        if not df_sym.empty:
                            df_sym = df_sym.reset_index()
                            date_col = "Date" if "Date" in df_sym.columns else df_sym.columns[0]
                            df_sym["Date"] = pd.to_datetime(df_sym[date_col]).dt.tz_localize(None)
                            df_sym = df_sym.sort_values("Date").reset_index(drop=True)
                            df_sym.to_csv(get_cache_path(sym), index=False)
                            loaded_data[sym] = df_sym
            except Exception as e:
                print(f"[WARN] Error batch downloading: {e}")

        # Mark cache as fully synced
        try:
            with open(CACHE_SYNC_FLAG, "w", encoding="utf-8") as f:
                f.write(f"Synced on {datetime.datetime.now().isoformat()}\n")
        except Exception:
            pass

    return loaded_data


def precalculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Precompute EMA 10, 20, 50, 200, RSI 14, ATR 14, 20 Vol SMA, and 20d Return."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    df["EMA_10"] = ta.trend.EMAIndicator(close=close, window=10).ema_indicator()
    df["EMA_20"] = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
    df["EMA_50"] = ta.trend.EMAIndicator(close=close, window=50).ema_indicator()
    df["EMA_200"] = ta.trend.EMAIndicator(close=close, window=200).ema_indicator()
    df["RSI_14"] = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    df["ATR_14"] = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    df["VOL_SMA_20"] = volume.rolling(window=20).mean()
    df["Ret_20"] = (close - close.shift(20)) / close.shift(20)

    df["DateStr"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df


def simulate_period(
    start_date: str,
    end_date: str,
    period_label: str,
    nifty_dict: Dict[str, Any],
    all_nifty_dates: List[str],
    stock_lookups: Dict[str, Dict[str, Any]],
    universe: List[str],
    results_csv: str
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    Run backtest simulation for a specific historical horizon:
    - 5 concurrent slots with Rs 2,000 risk per trade (1.5x ATR stop).
    - Rs 40,000 max capital per slot.
    - Top Relative Strength selection (no sector restriction, strictly matching live bot.py).
    - 2-Stage ATR Chandelier Trailing Engine:
      * Stage 1: Breakeven Lock at +1.5R (+Rs 3,000).
      * Stage 2: Trail Highest Close - (2.5 * ATR) from +2.5R (+Rs 5,000) onward.
      * Hard stop on full position upon trailing stop touch.
    """
    # Filter dates for this period
    period_dates = [d for d in all_nifty_dates if start_date <= d <= end_date]
    if not period_dates:
        raise ValueError(f"No trading dates found between {start_date} and {end_date}")

    print(f"\n[RUN] Simulating {period_label} ({period_dates[0]} to {period_dates[-1]}, {len(period_dates)} sessions)...")

    cash = INITIAL_CAPITAL
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    trade_id_counter = 1

    for d in period_dates:
        # -------------------------------------------------------------
        # Step 1: Manage Existing Open Positions (Chandelier Trailing)
        # -------------------------------------------------------------
        still_open = []
        for pos in open_positions:
            sym = pos["symbol"]
            bar = stock_lookups.get(sym, {}).get(d)
            if bar is None:
                still_open.append(pos)
                continue

            open_p = bar["Open"]
            high = bar["High"]
            low = bar["Low"]
            close = bar["Close"]
            atr = bar["ATR_14"]
            entry_price = pos["entry_price"]
            qty = pos["qty"]
            per_share_risk = pos["per_share_risk"]
            trade_risk = pos["trade_risk"]

            # Check if current trailing stop is breached today
            if low <= pos["stop_loss"]:
                exit_price = pos["stop_loss"]
                if open_p < pos["stop_loss"]:
                    exit_price = min(open_p, pos["stop_loss"])

                gross = (exit_price - entry_price) * qty
                turnover = (entry_price + exit_price) * qty
                friction = round(FRICTION_RATE * turnover, 2)
                net_pnl = round(gross - friction, 2)
                cash += round((exit_price * qty) - friction, 2)

                pos["exit_date"] = d
                pos["exit_price"] = round(exit_price, 2)
                pos["gross_pnl"] = round(gross, 2)
                pos["friction"] = friction
                pos["total_pnl"] = net_pnl
                pos["final_stop_loss"] = round(pos["stop_loss"], 2)

                if pos["stage"] == 2:
                    pos["exit_reason"] = f"Chandelier Trail (Peak Rs {pos['highest_close']:.2f} - 2.5x ATR)"
                elif pos["stage"] == 1:
                    pos["exit_reason"] = "Breakeven Lock (+1.5R)"
                else:
                    pos["exit_reason"] = "Initial Stop-Loss (-1.5x ATR)"

                pos["status"] = "CLOSED"
                closed_trades.append(pos)

            else:
                # Update highest daily close
                if close > pos["highest_close"]:
                    pos["highest_close"] = close

                # Track peak unrealized profit and R achieved
                unrealized_peak_gain = high - entry_price
                current_r = unrealized_peak_gain / per_share_risk
                if current_r > pos["max_r_reached"]:
                    pos["max_r_reached"] = round(current_r, 2)

                # Stage 2: When unrealized gain reaches +2.5R (+Rs 5,000)
                if current_r >= 2.5 or (unrealized_peak_gain * qty) >= 5000.0:
                    pos["stage"] = 2
                    chandelier_stop = round(pos["highest_close"] - (2.5 * atr), 2)
                    if chandelier_stop > pos["stop_loss"]:
                        pos["stop_loss"] = chandelier_stop
                    if pos["stop_loss"] < entry_price:
                        pos["stop_loss"] = entry_price

                # Stage 1: When unrealized gain reaches +1.5R (+Rs 3,000)
                elif current_r >= 1.5 or (unrealized_peak_gain * qty) >= 3000.0:
                    if pos["stage"] < 1:
                        pos["stage"] = 1
                    if entry_price > pos["stop_loss"]:
                        pos["stop_loss"] = entry_price

                still_open.append(pos)

        open_positions = still_open

        # -------------------------------------------------------------
        # Step 2: Compute Current Mark-to-Market Portfolio Equity
        # -------------------------------------------------------------
        current_open_val = 0.0
        for p in open_positions:
            sym = p["symbol"]
            curr_bar = stock_lookups.get(sym, {}).get(d)
            curr_close = curr_bar["Close"] if curr_bar else p["entry_price"]
            current_open_val += curr_close * p["qty"]

        current_portfolio_equity = cash + current_open_val

        # -------------------------------------------------------------
        # Step 3: Benchmark Regime Filter & New Setup Screening
        # -------------------------------------------------------------
        available_slots = MAX_CONCURRENT_POSITIONS - len(open_positions)
        nifty_bar = nifty_dict.get(d)
        is_bullish_regime = nifty_bar.get("is_bullish", False) if nifty_bar else False
        nifty_ret20 = nifty_bar.get("Ret_20", 0.0) if nifty_bar else 0.0

        if available_slots > 0 and is_bullish_regime:
            candidates = []
            open_symbols = {p["symbol"] for p in open_positions}

            for sym in universe:
                if sym in open_symbols:
                    continue

                bar = stock_lookups.get(sym, {}).get(d)
                if bar is None:
                    continue

                c = bar["Close"]
                o = bar["Open"]
                l = bar["Low"]
                v = bar["Volume"]
                e20 = bar["EMA_20"]
                e50 = bar["EMA_50"]
                e200 = bar["EMA_200"]
                rsi = bar["RSI_14"]
                atr = bar["ATR_14"]
                vol_sma = bar["VOL_SMA_20"]

                # Strict Match to bot.py Confluence:
                # 1. Close > 200 EMA & 20 EMA > 50 EMA
                # 2. Low <= 20 EMA, Close > 20 EMA, Close >= Open
                # 3. RSI [40, 65]
                # 4. Volume >= 0.85 * SMA(20)
                if (
                    (c > e200) and (e20 > e50) and
                    (l <= e20) and (c > e20) and (c >= o) and
                    (40.0 <= rsi <= 65.0) and
                    (vol_sma > 0 and v >= 0.85 * vol_sma) and
                    (atr > 0.05)
                ):
                    rs = (bar["Ret_20"] - nifty_ret20) * 100.0
                    candidates.append({
                        "symbol": sym,
                        "bar": bar,
                        "relative_strength": rs,
                    })

            # Rank strictly by 20-day Relative Strength descending (NO sector restrictions)
            candidates.sort(key=lambda x: x["relative_strength"], reverse=True)

            # Enter top candidates
            for cand in candidates:
                if available_slots <= 0:
                    break

                sym = cand["symbol"]
                bar = cand["bar"]
                entry_price = bar["Close"]
                atr = bar["ATR_14"]
                per_share_risk = round(1.5 * atr, 2)
                if per_share_risk <= 0.05:
                    continue

                # Position sizing matching live bot.py:
                # floor(2000 / per_share_risk), bounded by min(cash, 40000)
                raw_qty = int(math.floor(RISK_PER_TRADE / per_share_risk))
                max_qty_by_slot = int(math.floor(min(cash, MAX_CAPITAL_PER_SLOT) / entry_price))
                qty = min(raw_qty, max_qty_by_slot)

                if qty >= 1:
                    total_cost = round(qty * entry_price, 2)
                    cash = round(cash - total_cost, 2)
                    available_slots -= 1

                    initial_stop_loss = round(entry_price - per_share_risk, 2)
                    actual_trade_risk = round(qty * per_share_risk, 2)

                    new_pos = {
                        "id": trade_id_counter,
                        "symbol": sym,
                        "entry_date": d,
                        "entry_price": entry_price,
                        "qty": qty,
                        "cost": total_cost,
                        "per_share_risk": per_share_risk,
                        "trade_risk": actual_trade_risk,
                        "atr": atr,
                        "initial_stop_loss": initial_stop_loss,
                        "stop_loss": initial_stop_loss,
                        "highest_close": entry_price,
                        "max_r_reached": 0.0,
                        "stage": 0,
                        "status": "OPEN",
                        "exit_date": None,
                        "exit_price": None,
                        "gross_pnl": 0.0,
                        "friction": 0.0,
                        "total_pnl": 0.0,
                        "final_stop_loss": initial_stop_loss,
                        "exit_reason": "",
                        "relative_strength": round(cand["relative_strength"], 2),
                        "rsi": round(bar["RSI_14"], 2),
                    }
                    trade_id_counter += 1
                    open_positions.append(new_pos)

        # -------------------------------------------------------------
        # Step 4: Record Daily Valuation
        # -------------------------------------------------------------
        eod_open_val = 0.0
        for p in open_positions:
            sym = p["symbol"]
            curr_bar = stock_lookups.get(sym, {}).get(d)
            curr_close = curr_bar["Close"] if curr_bar else p["entry_price"]
            eod_open_val += curr_close * p["qty"]

        daily_equity = cash + eod_open_val
        equity_curve.append({
            "Date": d,
            "Cash": round(cash, 2),
            "OpenPositionsVal": round(eod_open_val, 2),
            "TotalEquity": round(daily_equity, 2),
            "ActiveTrades": len(open_positions),
        })

    # Close any open positions on the final backtest date at market close
    final_date = period_dates[-1]
    for p in open_positions:
        sym = p["symbol"]
        curr_bar = stock_lookups.get(sym, {}).get(final_date)
        final_close = curr_bar["Close"] if curr_bar else p["entry_price"]
        gross = (final_close - p["entry_price"]) * p["qty"]
        turnover = (p["entry_price"] + final_close) * p["qty"]
        friction = round(FRICTION_RATE * turnover, 2)
        net = round(gross - friction, 2)

        p["exit_date"] = final_date
        p["exit_price"] = final_close
        p["gross_pnl"] = round(gross, 2)
        p["friction"] = friction
        p["total_pnl"] = net
        p["final_stop_loss"] = round(p["stop_loss"], 2)
        p["exit_reason"] = "Period Ended (Mark to Market)"
        p["status"] = "CLOSED"
        closed_trades.append(p)

    df_trades = pd.DataFrame(closed_trades)
    df_equity = pd.DataFrame(equity_curve)

    # -------------------------------------------------------------
    # Performance Metrics
    # -------------------------------------------------------------
    start_equity = INITIAL_CAPITAL
    final_equity = df_equity["TotalEquity"].iloc[-1]
    total_net_pnl = final_equity - start_equity
    strategy_return_pct = (total_net_pnl / start_equity) * 100.0

    start_dt = pd.to_datetime(period_dates[0])
    end_dt = pd.to_datetime(period_dates[-1])
    years = (end_dt - start_dt).days / 365.25

    # Strategy CAGR
    if final_equity > 0 and years > 0:
        strategy_cagr = ((final_equity / start_equity) ** (1.0 / years) - 1.0) * 100.0
    else:
        strategy_cagr = 0.0

    # Nifty 50 Benchmark Buy & Hold
    nifty_start_close = nifty_dict[period_dates[0]]["Close"]
    nifty_end_close = nifty_dict[period_dates[-1]]["Close"]
    nifty_return_pct = ((nifty_end_close - nifty_start_close) / nifty_start_close) * 100.0
    nifty_cagr = (((nifty_end_close / nifty_start_close) ** (1.0 / years)) - 1.0) * 100.0

    excess_alpha_cagr = strategy_cagr - nifty_cagr

    total_trades_count = len(df_trades)
    winning_trades = df_trades[df_trades["total_pnl"] > 0] if total_trades_count > 0 else pd.DataFrame()
    losing_trades = df_trades[df_trades["total_pnl"] <= 0] if total_trades_count > 0 else pd.DataFrame()
    win_count = len(winning_trades)
    loss_count = len(losing_trades)
    win_rate_pct = (win_count / total_trades_count * 100.0) if total_trades_count > 0 else 0.0

    gross_profit = winning_trades["total_pnl"].sum() if win_count > 0 else 0.0
    gross_loss = abs(losing_trades["total_pnl"].sum()) if loss_count > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # Maximum Drawdown %
    df_equity["Peak"] = df_equity["TotalEquity"].cummax()
    df_equity["Drawdown"] = (df_equity["TotalEquity"] - df_equity["Peak"]) / df_equity["Peak"] * 100.0
    max_drawdown_pct = abs(df_equity["Drawdown"].min())

    expectancy_per_trade = (total_net_pnl / total_trades_count) if total_trades_count > 0 else 0.0

    stage_0_exits = len(df_trades[df_trades["stage"] == 0]) if total_trades_count > 0 else 0
    stage_1_exits = len(df_trades[df_trades["stage"] == 1]) if total_trades_count > 0 else 0
    stage_2_exits = len(df_trades[df_trades["stage"] == 2]) if total_trades_count > 0 else 0

    metrics = {
        "period_label": period_label,
        "start_date": period_dates[0],
        "end_date": period_dates[-1],
        "trading_sessions": len(period_dates),
        "years": round(years, 2),
        "initial_capital": start_equity,
        "final_equity": round(final_equity, 2),
        "total_net_pnl": round(total_net_pnl, 2),
        "strategy_return_pct": round(strategy_return_pct, 2),
        "strategy_cagr": round(strategy_cagr, 2),
        "nifty_return_pct": round(nifty_return_pct, 2),
        "nifty_cagr": round(nifty_cagr, 2),
        "excess_alpha_cagr": round(excess_alpha_cagr, 2),
        "total_trades": total_trades_count,
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "win_rate_pct": round(win_rate_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "expectancy_per_trade": round(expectancy_per_trade, 2),
        "stage_0_exits": stage_0_exits,
        "stage_1_exits": stage_1_exits,
        "stage_2_exits": stage_2_exits,
    }

    # Save individual trade logs
    export_cols = [
        "id", "symbol", "entry_date", "entry_price", "qty", "cost",
        "trade_risk", "initial_stop_loss", "final_stop_loss", "highest_close", "max_r_reached",
        "stage", "exit_date", "exit_price", "gross_pnl", "friction", "total_pnl",
        "exit_reason", "relative_strength", "rsi"
    ]
    available_cols = [c for c in export_cols if c in df_trades.columns]
    df_trades_export = df_trades[available_cols].copy()
    if "total_pnl" in df_trades_export.columns and "cost" in df_trades_export.columns:
        df_trades_export["return_pct"] = round((df_trades_export["total_pnl"] / df_trades_export["cost"]) * 100.0, 2)

    df_trades_export.to_csv(results_csv, index=False)
    print(f"[EXPORT] Saved {len(df_trades_export)} trade logs to: {os.path.basename(results_csv)}")

    return metrics, df_trades


def run_all_horizons():
    """Main execution orchestrating 3-Year, 5-Year, and 10-Year backtests."""
    print("=" * 84)
    print("      MULTI-PERIOD BACKTEST ENGINE: 5-SLOT ATR CHANDELIER STRATEGY")
    print("=" * 84)

    universe = load_nifty200_universe()
    all_symbols = ["^NSEI"] + universe

    # 1. Load or fetch historical dataset
    raw_data = load_or_fetch_all_data(all_symbols)

    if "^NSEI" not in raw_data or raw_data["^NSEI"].empty:
        raise ValueError("Failed to load benchmark ^NSEI data.")

    # 2. Precompute indicators
    print("\n[PRECOMPUTE] Calculating technical indicators across all symbols...")
    nifty_df = raw_data["^NSEI"].copy()
    nifty_df["EMA_50"] = ta.trend.EMAIndicator(close=nifty_df["Close"], window=50).ema_indicator()
    nifty_df["Ret_20"] = (nifty_df["Close"] - nifty_df["Close"].shift(20)) / nifty_df["Close"].shift(20)
    nifty_df["DateStr"] = nifty_df["Date"].dt.strftime("%Y-%m-%d")

    nifty_dict = {}
    for _, row in nifty_df.iterrows():
        d_str = row["DateStr"]
        c = float(row["Close"])
        e50 = float(row["EMA_50"]) if pd.notna(row["EMA_50"]) else 0.0
        r20 = float(row["Ret_20"]) if pd.notna(row["Ret_20"]) else 0.0
        nifty_dict[d_str] = {
            "Close": c,
            "EMA_50": e50,
            "Ret_20": r20,
            "is_bullish": (c > e50) if e50 > 0 else True,
        }

    all_nifty_dates = sorted(list(nifty_dict.keys()))

    stock_lookups = {}
    valid_stocks = 0
    for sym in universe:
        if sym in raw_data and len(raw_data[sym]) >= 100:
            df = precalculate_indicators(raw_data[sym].copy())
            records = {}
            for _, r in df.iterrows():
                records[r["DateStr"]] = {
                    "Open": float(r["Open"]),
                    "High": float(r["High"]),
                    "Low": float(r["Low"]),
                    "Close": float(r["Close"]),
                    "Volume": float(r["Volume"]),
                    "EMA_10": float(r["EMA_10"]) if pd.notna(r["EMA_10"]) else 0.0,
                    "EMA_20": float(r["EMA_20"]) if pd.notna(r["EMA_20"]) else 0.0,
                    "EMA_50": float(r["EMA_50"]) if pd.notna(r["EMA_50"]) else 0.0,
                    "EMA_200": float(r["EMA_200"]) if pd.notna(r["EMA_200"]) else 0.0,
                    "RSI_14": float(r["RSI_14"]) if pd.notna(r["RSI_14"]) else 50.0,
                    "ATR_14": float(r["ATR_14"]) if pd.notna(r["ATR_14"]) else 0.0,
                    "VOL_SMA_20": float(r["VOL_SMA_20"]) if pd.notna(r["VOL_SMA_20"]) else 0.0,
                    "Ret_20": float(r["Ret_20"]) if pd.notna(r["Ret_20"]) else 0.0,
                }
            stock_lookups[sym] = records
            valid_stocks += 1

    print(f"[PRECOMPUTE] Ready: {valid_stocks} Nifty 200 constituents indexed with indicators.")

    # 3. Define Periods
    horizons = [
        {
            "label": "3-Year",
            "start": "2023-11-09",
            "end": "2026-11-09",
            "csv": RESULTS_3Y_CSV
        },
        {
            "label": "5-Year",
            "start": "2021-11-09",
            "end": "2026-11-09",
            "csv": RESULTS_5Y_CSV
        },
        {
            "label": "10-Year",
            "start": "2016-11-09",
            "end": "2026-11-09",
            "csv": RESULTS_10Y_CSV
        },
    ]

    results = []

    # 4. Run Sequential Simulations
    for h in horizons:
        m, _ = simulate_period(
            start_date=h["start"],
            end_date=h["end"],
            period_label=h["label"],
            nifty_dict=nifty_dict,
            all_nifty_dates=all_nifty_dates,
            stock_lookups=stock_lookups,
            universe=universe,
            results_csv=h["csv"]
        )
        results.append(m)

    # 5. Format & Print Consolidated Comparison Markdown Table
    m3, m5, m10 = results[0], results[1], results[2]

    print("\n" + "=" * 94)
    print("            CONSOLIDATED MULTI-PERIOD PERFORMANCE SCORECARD")
    print("=" * 94)

    comparison_md = f"""
| Metric | 3-Year ({m3['start_date'][:4]}-{m3['end_date'][:4]}) | 5-Year ({m5['start_date'][:4]}-{m5['end_date'][:4]}) | 10-Year ({m10['start_date'][:4]}-{m10['end_date'][:4]}) |
| :--- | :---: | :---: | :---: |
| **Historical Date Range** | {m3['start_date']} to {m3['end_date']} | {m5['start_date']} to {m5['end_date']} | {m10['start_date']} to {m10['end_date']} |
| **Trading Sessions (Duration)** | {m3['trading_sessions']} sessions ({m3['years']}y) | {m5['trading_sessions']} sessions ({m5['years']}y) | {m10['trading_sessions']} sessions ({m10['years']}y) |
| **Total Trades Executed** | **{m3['total_trades']}** | **{m5['total_trades']}** | **{m10['total_trades']}** |
| **Winning Trades / Losing Trades** | {m3['winning_trades']} / {m3['losing_trades']} | {m5['winning_trades']} / {m5['losing_trades']} | {m10['winning_trades']} / {m10['losing_trades']} |
| **Win Rate (%)** | **{m3['win_rate_pct']:.2f}%** | **{m5['win_rate_pct']:.2f}%** | **{m10['win_rate_pct']:.2f}%** |
| **Profit Factor** | **{m3['profit_factor']:.2f}** | **{m5['profit_factor']:.2f}** | **{m10['profit_factor']:.2f}** |
| **Maximum Drawdown (%)** | **{m3['max_drawdown_pct']:.2f}%** | **{m5['max_drawdown_pct']:.2f}%** | **{m10['max_drawdown_pct']:.2f}%** |
| **Initial Capital** | ₹{m3['initial_capital']:,.2f} | ₹{m5['initial_capital']:,.2f} | ₹{m10['initial_capital']:,.2f} |
| **Final Portfolio Equity** | ₹{m3['final_equity']:,.2f} | ₹{m5['final_equity']:,.2f} | ₹{m10['final_equity']:,.2f} |
| **Strategy Total Return (%)** | **+{m3['strategy_return_pct']:.2f}%** | **+{m5['strategy_return_pct']:.2f}%** | **+{m10['strategy_return_pct']:.2f}%** |
| **Strategy CAGR (%)** | **{m3['strategy_cagr']:+.2f}%** | **{m5['strategy_cagr']:+.2f}%** | **{m10['strategy_cagr']:+.2f}%** |
| **Nifty 50 Total Return (%)** | +{m3['nifty_return_pct']:.2f}% | +{m5['nifty_return_pct']:.2f}% | +{m10['nifty_return_pct']:.2f}% |
| **Nifty 50 Benchmark CAGR (%)** | {m3['nifty_cagr']:+.2f}% | {m5['nifty_cagr']:+.2f}% | {m10['nifty_cagr']:+.2f}% |
| **Excess Alpha CAGR (%)** | **{m3['excess_alpha_cagr']:+.2f}%** | **{m5['excess_alpha_cagr']:+.2f}%** | **{m10['excess_alpha_cagr']:+.2f}%** |
| **Expectancy per Trade (Rs)** | **+₹{m3['expectancy_per_trade']:,.2f}** | **+₹{m5['expectancy_per_trade']:,.2f}** | **+₹{m10['expectancy_per_trade']:,.2f}** |
| **Exit Stages (SL / BE / Trail)** | {m3['stage_0_exits']} / {m3['stage_1_exits']} / {m3['stage_2_exits']} | {m5['stage_0_exits']} / {m5['stage_1_exits']} / {m5['stage_2_exits']} | {m10['stage_0_exits']} / {m10['stage_1_exits']} / {m10['stage_2_exits']} |
"""

    print(comparison_md.strip())
    print("=" * 94 + "\n")


if __name__ == "__main__":
    run_all_horizons()
