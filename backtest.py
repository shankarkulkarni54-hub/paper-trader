import datetime
import math
import os
import sys
import urllib.request
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd
import yfinance as yf
import ta

from bot import load_nifty200_universe

# Backtest Configuration
INITIAL_CAPITAL = 200000.0
MAX_CONCURRENT_POSITIONS = 4
MAX_SLOT_CAPITAL = INITIAL_CAPITAL / MAX_CONCURRENT_POSITIONS  # Rs 50,000 per slot
RISK_PER_TRADE = 2000.0                                       # Rs 2,000 (1% of starting capital)
FRICTION_RATE = 0.0015                                        # 0.15% round-trip brokerage & friction

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
RESULTS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results.csv")

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


def load_or_fetch_all_data(symbols: List[str], period: str = "4y") -> Dict[str, pd.DataFrame]:
    """
    Load data from .cache/ if available, otherwise batch download from yfinance
    and cache locally to .cache/<symbol>.csv for subsequent instant runs.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    loaded_data = {}
    missing_symbols = []

    print("[DATA] Checking local cache in .cache/ ...")
    for sym in symbols:
        cache_file = get_cache_path(sym)
        if os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file)
                if not df.empty and "Close" in df.columns and len(df) >= 100:
                    date_col = "Date" if "Date" in df.columns else ("Datetime" if "Datetime" in df.columns else df.columns[0])
                    df["Date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None)
                    df = df.sort_values("Date").reset_index(drop=True)
                    loaded_data[sym] = df
                else:
                    missing_symbols.append(sym)
            except Exception:
                missing_symbols.append(sym)
        else:
            missing_symbols.append(sym)

    print(f"[DATA] Found {len(loaded_data)} symbols in cache. {len(missing_symbols)} to download.")

    if missing_symbols:
        print(f"[DATA] Downloading {len(missing_symbols)} symbols via yfinance (period='{period}')...")
        try:
            data = yf.download(
                tickers=missing_symbols,
                period=period,
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=True
            )

            if isinstance(data.columns, pd.MultiIndex):
                has_level0 = any(s in data.columns.levels[0] for s in missing_symbols)
                for sym in missing_symbols:
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

                            # Save to cache
                            df_sym.to_csv(get_cache_path(sym), index=False)
                            loaded_data[sym] = df_sym
                    except Exception as e:
                        pass
            else:
                if len(missing_symbols) == 1:
                    sym = missing_symbols[0]
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

    # Format date as YYYY-MM-DD string for exact day lookups
    df["DateStr"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df


def run_backtest() -> Tuple[Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Execute the full 3-year backtest simulation."""
    universe = load_nifty200_universe()
    all_symbols = ["^NSEI"] + universe

    raw_data = load_or_fetch_all_data(all_symbols, period="4y")

    if "^NSEI" not in raw_data or raw_data["^NSEI"].empty:
        raise ValueError("Failed to obtain benchmark ^NSEI data.")

    print("\n[PRECOMPUTE] Calculating technical indicators across all symbols...")
    # Precalculate Nifty 50
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

    # Precalculate all stock indicators & create fast date lookup
    stock_lookups = {}
    valid_stocks = 0
    for sym in universe:
        if sym in raw_data and len(raw_data[sym]) >= 200:
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

    print(f"[PRECOMPUTE] Precomputed indicators for {valid_stocks} Nifty 200 stocks.")

    # Determine 3-year backtest date range
    all_nifty_dates = sorted(list(nifty_dict.keys()))
    last_date = all_nifty_dates[-1]
    last_dt = pd.to_datetime(last_date)
    start_dt = last_dt - pd.DateOffset(years=3)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    # Filter dates that fall within the last 3 years
    backtest_dates = [d for d in all_nifty_dates if d >= start_date_str]

    print(f"\n[SIMULATION] Starting 3-year backtest:")
    print(f"             Window: {backtest_dates[0]} to {backtest_dates[-1]} ({len(backtest_dates)} trading sessions)")
    print(f"             Initial Capital: Rs {INITIAL_CAPITAL:,.2f} | Max Slots: {MAX_CONCURRENT_POSITIONS}")

    # Simulation State
    cash = INITIAL_CAPITAL
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    trade_id_counter = 1

    for d in backtest_dates:
        # -------------------------------------------------------------
        # Step 1: Manage Existing Open Positions (Hybrid Exits)
        # -------------------------------------------------------------
        still_open = []
        for pos in open_positions:
            sym = pos["symbol"]
            bar = stock_lookups.get(sym, {}).get(d)
            if bar is None:
                still_open.append(pos)
                continue

            high = bar["High"]
            low = bar["Low"]
            close = bar["Close"]
            ema_10 = bar["EMA_10"]
            entry_price = pos["entry_price"]

            # Phase A: Initial Position (Waiting for Target 1 or SL)
            if pos["status"] == "OPEN":
                # Check Initial Stop Loss (-1.5x ATR)
                if low <= pos["stop_loss"]:
                    exit_price = pos["stop_loss"]
                    qty = pos["remaining_qty"]
                    gross = (exit_price - entry_price) * qty
                    turnover = (entry_price + exit_price) * qty
                    friction = round(FRICTION_RATE * turnover, 2)
                    net_pnl = round(gross - friction, 2)
                    cash += round((exit_price * qty) - friction, 2)

                    pos["exit_date"] = d
                    pos["exit_price"] = exit_price
                    pos["status"] = "CLOSED_SL"
                    pos["total_pnl"] = net_pnl
                    pos["exit_reason"] = "Stop Loss (-1.5x ATR)"
                    pos["remaining_qty"] = 0
                    closed_trades.append(pos)

                # Check Target 1 (+2R)
                elif high >= pos["target_1"]:
                    exit_price = pos["target_1"]
                    qty_exit = max(1, pos["remaining_qty"] // 2) if pos["remaining_qty"] > 1 else 1
                    qty_runner = pos["remaining_qty"] - qty_exit

                    gross = (exit_price - entry_price) * qty_exit
                    turnover = (entry_price + exit_price) * qty_exit
                    friction = round(FRICTION_RATE * turnover, 2)
                    t1_pnl = round(gross - friction, 2)
                    cash += round((exit_price * qty_exit) - friction, 2)

                    pos["t1_exit_date"] = d
                    pos["t1_exit_price"] = exit_price
                    pos["t1_qty"] = qty_exit
                    pos["t1_pnl"] = t1_pnl

                    if qty_runner > 0:
                        pos["remaining_qty"] = qty_runner
                        pos["stop_loss"] = entry_price  # Move Stop to Breakeven
                        pos["status"] = "OPEN_RUNNER"
                        still_open.append(pos)
                    else:
                        pos["exit_date"] = d
                        pos["exit_price"] = exit_price
                        pos["status"] = "CLOSED_TARGET"
                        pos["total_pnl"] = t1_pnl
                        pos["exit_reason"] = "Target 1 Hit (Full Exit)"
                        pos["remaining_qty"] = 0
                        closed_trades.append(pos)
                else:
                    still_open.append(pos)

            # Phase B: Runner Active (50% remaining, Stop at Breakeven, trailing 10 EMA)
            elif pos["status"] == "OPEN_RUNNER":
                rem_qty = pos["remaining_qty"]
                breakeven_sl = pos["stop_loss"]  # Already set to entry_price

                # Check Breakeven Stop
                if low <= breakeven_sl:
                    exit_price = breakeven_sl
                    gross = (exit_price - entry_price) * rem_qty  # 0
                    turnover = (entry_price + exit_price) * rem_qty
                    friction = round(FRICTION_RATE * turnover, 2)
                    runner_pnl = round(gross - friction, 2)
                    cash += round((exit_price * rem_qty) - friction, 2)

                    pos["runner_exit_date"] = d
                    pos["runner_exit_price"] = exit_price
                    pos["runner_qty"] = rem_qty
                    pos["runner_pnl"] = runner_pnl
                    pos["exit_date"] = d
                    pos["exit_price"] = exit_price
                    pos["status"] = "CLOSED_TRAIL"
                    pos["total_pnl"] = round(pos["t1_pnl"] + runner_pnl, 2)
                    pos["exit_reason"] = "Runner Breakeven Stop"
                    pos["remaining_qty"] = 0
                    closed_trades.append(pos)

                # Check Trailing Exit (Close < 10-day EMA)
                elif close < ema_10:
                    exit_price = close
                    gross = (exit_price - entry_price) * rem_qty
                    turnover = (entry_price + exit_price) * rem_qty
                    friction = round(FRICTION_RATE * turnover, 2)
                    runner_pnl = round(gross - friction, 2)
                    cash += round((exit_price * rem_qty) - friction, 2)

                    pos["runner_exit_date"] = d
                    pos["runner_exit_price"] = exit_price
                    pos["runner_qty"] = rem_qty
                    pos["runner_pnl"] = runner_pnl
                    pos["exit_date"] = d
                    pos["exit_price"] = exit_price
                    pos["status"] = "CLOSED_TRAIL"
                    pos["total_pnl"] = round(pos["t1_pnl"] + runner_pnl, 2)
                    pos["exit_reason"] = "Runner Trail Exit (Close < 10 EMA)"
                    pos["remaining_qty"] = 0
                    closed_trades.append(pos)
                else:
                    still_open.append(pos)

        open_positions = still_open

        # -------------------------------------------------------------
        # Step 2: Benchmark Regime Filter & New Setup Screening
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
                h = bar["High"]
                l = bar["Low"]
                v = bar["Volume"]
                e20 = bar["EMA_20"]
                e50 = bar["EMA_50"]
                e200 = bar["EMA_200"]
                rsi = bar["RSI_14"]
                atr = bar["ATR_14"]
                vol_sma = bar["VOL_SMA_20"]

                # Confluence Setup Rules:
                # 1. Macro Trend: Close > 200 EMA and 20 EMA > 50 EMA
                # 2. Pullback Dynamic: Low <= 20 EMA, Close > 20 EMA, Close >= Open
                # 3. RSI Filter: 45 <= RSI <= 60
                # 4. Volume Filter: Volume >= 1.25 * 20-day Volume SMA
                if (
                    (c > e200) and (e20 > e50) and
                    (l <= e20) and (c > e20) and (c >= o) and
                    (45.0 <= rsi <= 60.0) and
                    (vol_sma > 0 and v >= 1.25 * vol_sma) and
                    (atr > 0.05)
                ):
                    rs = (bar["Ret_20"] - nifty_ret20) * 100.0
                    candidates.append({
                        "symbol": sym,
                        "bar": bar,
                        "relative_strength": rs
                    })

            # Rank candidates by 20-day Relative Strength descending
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

                stop_loss = round(entry_price - per_share_risk, 2)
                target_1 = round(entry_price + (2.0 * per_share_risk), 2)

                # Volatility-adjusted risk sizing: floor(2000 / Per-Share Risk)
                raw_qty = int(math.floor(RISK_PER_TRADE / per_share_risk))
                # Capital slot limit: Rs 50,000 max per slot, bounded by available cash
                max_qty_by_slot = int(math.floor(min(cash, MAX_SLOT_CAPITAL) / entry_price))
                qty = min(raw_qty, max_qty_by_slot)

                if qty >= 1:
                    total_cost = round(qty * entry_price, 2)
                    cash = round(cash - total_cost, 2)
                    available_slots -= 1

                    new_pos = {
                        "id": trade_id_counter,
                        "symbol": sym,
                        "entry_date": d,
                        "entry_price": entry_price,
                        "initial_qty": qty,
                        "remaining_qty": qty,
                        "stop_loss": stop_loss,
                        "target_1": target_1,
                        "per_share_risk": per_share_risk,
                        "atr": atr,
                        "cost": total_cost,
                        "status": "OPEN",
                        "t1_exit_date": None,
                        "t1_exit_price": None,
                        "t1_qty": 0,
                        "t1_pnl": 0.0,
                        "runner_exit_date": None,
                        "runner_exit_price": None,
                        "runner_qty": 0,
                        "runner_pnl": 0.0,
                        "exit_date": None,
                        "exit_price": None,
                        "total_pnl": 0.0,
                        "exit_reason": "",
                        "relative_strength": round(cand["relative_strength"], 2),
                        "rsi": round(bar["RSI_14"], 2),
                    }
                    trade_id_counter += 1
                    open_positions.append(new_pos)

        # -------------------------------------------------------------
        # Step 3: Record Daily Portfolio Valuation
        # -------------------------------------------------------------
        open_val = 0.0
        for p in open_positions:
            sym = p["symbol"]
            curr_bar = stock_lookups.get(sym, {}).get(d)
            curr_close = curr_bar["Close"] if curr_bar else p["entry_price"]
            open_val += curr_close * p["remaining_qty"]

        daily_equity = cash + open_val
        equity_curve.append({
            "Date": d,
            "Cash": round(cash, 2),
            "OpenPositionsVal": round(open_val, 2),
            "TotalEquity": round(daily_equity, 2),
            "ActiveTrades": len(open_positions),
        })

    # Close any still-open positions at the end of the simulation for accurate accounting
    final_date = backtest_dates[-1]
    for p in open_positions:
        sym = p["symbol"]
        curr_bar = stock_lookups.get(sym, {}).get(final_date)
        final_close = curr_bar["Close"] if curr_bar else p["entry_price"]
        rem_qty = p["remaining_qty"]
        gross = (final_close - p["entry_price"]) * rem_qty
        turnover = (p["entry_price"] + final_close) * rem_qty
        friction = round(FRICTION_RATE * turnover, 2)
        net = round(gross - friction, 2)

        p["exit_date"] = final_date
        p["exit_price"] = final_close
        p["total_pnl"] = round(p.get("t1_pnl", 0.0) + net, 2)
        p["exit_reason"] = "Backtest Window Ended (Mark to Market)"
        closed_trades.append(p)

    df_trades = pd.DataFrame(closed_trades)
    df_equity = pd.DataFrame(equity_curve)

    # -------------------------------------------------------------
    # Performance Metrics Calculation
    # -------------------------------------------------------------
    start_equity = INITIAL_CAPITAL
    final_equity = df_equity["TotalEquity"].iloc[-1]
    total_net_pnl = final_equity - start_equity
    strategy_return_pct = (total_net_pnl / start_equity) * 100.0

    # Nifty 50 Buy & Hold Return over the exact same period
    nifty_start_close = nifty_dict[backtest_dates[0]]["Close"]
    nifty_end_close = nifty_dict[backtest_dates[-1]]["Close"]
    nifty_bh_return_pct = ((nifty_end_close - nifty_start_close) / nifty_start_close) * 100.0

    total_trades_count = len(df_trades)
    winning_trades = df_trades[df_trades["total_pnl"] > 0]
    losing_trades = df_trades[df_trades["total_pnl"] <= 0]
    win_count = len(winning_trades)
    loss_count = len(losing_trades)
    win_rate_pct = (win_count / total_trades_count * 100.0) if total_trades_count > 0 else 0.0

    gross_profit = winning_trades["total_pnl"].sum()
    gross_loss = abs(losing_trades["total_pnl"].sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # Max Drawdown Calculation
    df_equity["Peak"] = df_equity["TotalEquity"].cummax()
    df_equity["Drawdown"] = (df_equity["TotalEquity"] - df_equity["Peak"]) / df_equity["Peak"] * 100.0
    max_drawdown_pct = abs(df_equity["Drawdown"].min())

    avg_win = winning_trades["total_pnl"].mean() if win_count > 0 else 0.0
    avg_loss = losing_trades["total_pnl"].mean() if loss_count > 0 else 0.0
    avg_trade_pnl = df_trades["total_pnl"].mean() if total_trades_count > 0 else 0.0

    metrics = {
        "start_date": backtest_dates[0],
        "end_date": backtest_dates[-1],
        "trading_sessions": len(backtest_dates),
        "initial_capital": start_equity,
        "final_equity": final_equity,
        "total_net_pnl": total_net_pnl,
        "strategy_return_pct": strategy_return_pct,
        "nifty_bh_return_pct": nifty_bh_return_pct,
        "alpha_pct": strategy_return_pct - nifty_bh_return_pct,
        "total_trades": total_trades_count,
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "win_rate_pct": win_rate_pct,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_trade_pnl": avg_trade_pnl,
    }

    # Format and save backtest results CSV
    export_cols = [
        "id", "symbol", "entry_date", "entry_price", "initial_qty", "cost",
        "stop_loss", "target_1", "t1_exit_date", "t1_exit_price", "t1_qty", "t1_pnl",
        "runner_exit_date", "runner_exit_price", "runner_qty", "runner_pnl",
        "exit_date", "exit_price", "total_pnl", "exit_reason", "relative_strength", "rsi"
    ]
    available_cols = [c for c in export_cols if c in df_trades.columns]
    df_trades_export = df_trades[available_cols].copy()
    if "total_pnl" in df_trades_export.columns and "cost" in df_trades_export.columns:
        df_trades_export["return_pct"] = (df_trades_export["total_pnl"] / df_trades_export["cost"]) * 100.0

    df_trades_export.to_csv(RESULTS_CSV, index=False)
    print(f"\n[EXPORT] Successfully saved {len(df_trades_export)} individual trade logs to: {RESULTS_CSV}")

    return metrics, df_trades, df_equity


def print_backtest_summary(m: Dict[str, Any]) -> None:
    """Print an executive performance summary table to the terminal."""
    print("\n" + "=" * 80)
    print("      VOLATILITY-ADJUSTED SWING STRATEGY: 3-YEAR PERFORMANCE REPORT")
    print("=" * 80)
    print(f"Backtest Period        : {m['start_date']}  to  {m['end_date']} ({m['trading_sessions']} sessions)")
    print(f"Screening Universe     : Nifty 200 Daily (Macro Trend + 20 EMA Pullback Confluence)")
    print(f"Benchmark Regime Filter: Nifty 50 Close > 50-day EMA")
    print(f"Risk Management Model  : Rs 2,000 risk (1.5x ATR Stop) | Hybrid Exit (50% T1 / 50% 10-EMA Trail)")
    print("-" * 80)
    print(f"Initial Starting Capital: Rs {m['initial_capital']:>14,.2f}")
    print(f"Ending Portfolio Equity : Rs {m['final_equity']:>14,.2f}")
    print(f"Total Strategy Net P&L  : Rs {m['total_net_pnl']:>+14,.2f}")
    print(f"Strategy Total Return   :    {m['strategy_return_pct']:>13.2f} %")
    print(f"Nifty 50 Buy & Hold Ret :    {m['nifty_bh_return_pct']:>13.2f} %")
    print(f"Strategy Excess Alpha   :    {m['alpha_pct']:>+13.2f} %")
    print("-" * 80)
    print(f"Total Executed Trades   :    {m['total_trades']:>14d}")
    print(f"Winning Trades          :    {m['winning_trades']:>14d} ({m['win_rate_pct']:.1f}%)")
    print(f"Losing Trades           :    {m['losing_trades']:>14d} ({100 - m['win_rate_pct']:.1f}%)")
    print(f"Profit Factor           :    {m['profit_factor']:>14.2f}")
    print(f"Maximum Strategy DD (%) :    {m['max_drawdown_pct']:>13.2f} %")
    print("-" * 80)
    print(f"Gross Profits Realized  : Rs {m['gross_profit']:>14,.2f}")
    print(f"Gross Losses Incurred   : Rs {m['gross_loss']:>14,.2f}")
    print(f"Average Winning Trade   : Rs {m['avg_win']:>+14,.2f}")
    print(f"Average Losing Trade    : Rs {m['avg_loss']:>+14,.2f}")
    print(f"Expectancy per Trade    : Rs {m['avg_trade_pnl']:>+14,.2f}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    metrics, df_trades, df_equity = run_backtest()
    print_backtest_summary(metrics)
