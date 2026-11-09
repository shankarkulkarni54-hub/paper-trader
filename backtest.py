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

# Backtest Configuration (Dynamic Compounding & Sector Diversified Engine)
INITIAL_CAPITAL = 200000.0
MAX_CONCURRENT_POSITIONS = 5                     # 5 concurrent slots
RISK_PCT_PER_TRADE = 0.01                        # 1.0% dynamic risk of current portfolio equity
MAX_SLOT_PCT = 0.20                              # Max 20% capital deployed per slot
FRICTION_RATE = 0.0015                           # 0.15% round-trip friction / brokerage
STCG_TAX_RATE = 0.20                             # 20% Short-Term Capital Gains Tax

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
RESULTS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_results.csv")
LOCAL_CSV_NAME = "nifty200.csv"

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


def load_symbol_sector_map() -> Dict[str, str]:
    """Load primary sector/industry for each Nifty 200 stock from local CSV."""
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOCAL_CSV_NAME)
    sector_map = {}
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            for _, r in df.iterrows():
                if "Symbol" in r and "Industry" in r:
                    sym = f"{str(r['Symbol']).strip()}.NS"
                    ind = str(r["Industry"]).strip() if pd.notna(r["Industry"]) else "Diversified"
                    sector_map[sym] = ind
        except Exception as e:
            print(f"[WARN] Error reading sector map: {e}")
    return sector_map


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
                    except Exception:
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
    """Execute dynamic compounding, sector-diversified backtest simulation."""
    universe = load_nifty200_universe()
    all_symbols = ["^NSEI"] + universe

    sector_map = load_symbol_sector_map()
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

    # Precalculate stock indicators & create fast date lookup
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

    print(f"[PRECOMPUTE] Precomputed indicators for {valid_stocks} Nifty 200 stocks across sectors.")

    # Determine 3-year backtest date range
    all_nifty_dates = sorted(list(nifty_dict.keys()))
    last_date = all_nifty_dates[-1]
    last_dt = pd.to_datetime(last_date)
    start_dt = last_dt - pd.DateOffset(years=3)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    # Filter dates that fall within the last 3 years
    backtest_dates = [d for d in all_nifty_dates if d >= start_date_str]

    print(f"\n[SIMULATION] Starting 3-year backtest (Dynamic Compounding & Sector Diversified):")
    print(f"             Window: {backtest_dates[0]} to {backtest_dates[-1]} ({len(backtest_dates)} trading sessions)")
    print(f"             Initial Capital: Rs {INITIAL_CAPITAL:,.2f} | Max Concurrent Slots: {MAX_CONCURRENT_POSITIONS}")
    print(f"             Dynamic Risk: 1.0% of Portfolio Equity per trade | Max Slot Capital: 20% of Portfolio Equity")
    print(f"             Sector Limit: Max 1 active position per sector at any time")
    print(f"             Trailing Stop: Stage 1 (+1.5R Breakeven) -> Stage 2 (+2.5R Chandelier 2.5x ATR)")

    # Simulation State
    cash = INITIAL_CAPITAL
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    equity_curve: List[Dict[str, Any]] = []
    trade_id_counter = 1

    for d in backtest_dates:
        # -------------------------------------------------------------
        # Step 1: Manage Existing Open Positions (2-Stage Dynamic Trailing Stop)
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

            # Check if current stop-loss is breached today
            if low <= pos["stop_loss"]:
                # Full position exit at current stop-loss (or gap open if lower)
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
                    pos["exit_reason"] = f"Chandelier Trail Exit (Peak Close Rs {pos['highest_close']:.2f} - 2.5x ATR)"
                elif pos["stage"] == 1:
                    pos["exit_reason"] = "Breakeven Stop (Locked +1.5R)"
                else:
                    pos["exit_reason"] = "Initial Stop-Loss (-1.5x ATR)"

                pos["status"] = "CLOSED"
                closed_trades.append(pos)

            else:
                # Position not stopped out. Update highest close & evaluate ratcheting stops
                if close > pos["highest_close"]:
                    pos["highest_close"] = close

                # Track peak unrealized profit & R achieved
                unrealized_peak_gain = high - entry_price
                current_r = unrealized_peak_gain / per_share_risk
                if current_r > pos["max_r_reached"]:
                    pos["max_r_reached"] = round(current_r, 2)

                # Stage 2: When unrealized profit reaches +2.5R (or +2.5 * initial risk)
                if current_r >= 2.5 or (unrealized_peak_gain * qty) >= (2.5 * trade_risk):
                    pos["stage"] = 2
                    chandelier_stop = round(pos["highest_close"] - (2.5 * atr), 2)

                    # Stop-loss can only ratchet upward, never downward
                    if chandelier_stop > pos["stop_loss"]:
                        pos["stop_loss"] = chandelier_stop

                    # Guard: In Stage 2, stop must at least be Breakeven
                    if pos["stop_loss"] < entry_price:
                        pos["stop_loss"] = entry_price

                # Stage 1: When unrealized profit reaches +1.5R (or +1.5 * initial risk)
                elif current_r >= 1.5 or (unrealized_peak_gain * qty) >= (1.5 * trade_risk):
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
            # Sector Diversification: Map active sectors currently held
            active_sectors = {p["sector"] for p in open_positions}

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

                # Setup Confluence Rules:
                # 1. Macro Trend: Close > 200 EMA and 20 EMA > 50 EMA
                # 2. Pullback Dynamic: Low <= 20 EMA, Close > 20 EMA, Close >= Open
                # 3. RSI Filter: 40.0 <= RSI <= 65.0
                # 4. Volume Filter: Volume >= 0.85 * 20-day Volume SMA
                if (
                    (c > e200) and (e20 > e50) and
                    (l <= e20) and (c > e20) and (c >= o) and
                    (40.0 <= rsi <= 65.0) and
                    (vol_sma > 0 and v >= 0.85 * vol_sma) and
                    (atr > 0.05)
                ):
                    rs = (bar["Ret_20"] - nifty_ret20) * 100.0
                    sector = sector_map.get(sym, "Diversified")
                    candidates.append({
                        "symbol": sym,
                        "bar": bar,
                        "relative_strength": rs,
                        "sector": sector,
                    })

            # Rank candidates by 20-day Relative Strength descending
            candidates.sort(key=lambda x: x["relative_strength"], reverse=True)

            # Enter top candidates with Sector Diversification Guard (Max 1 position per sector)
            for cand in candidates:
                if available_slots <= 0:
                    break

                sector = cand["sector"]

                # Sector Diversification Guard: Skip if sector is already held in portfolio
                if sector in active_sectors:
                    continue

                sym = cand["symbol"]
                bar = cand["bar"]
                entry_price = bar["Close"]
                atr = bar["ATR_14"]
                per_share_risk = round(1.5 * atr, 2)
                if per_share_risk <= 0.05:
                    continue

                # Dynamic Compounding: 1.0% of Current Portfolio Equity
                dynamic_risk = current_portfolio_equity * RISK_PCT_PER_TRADE
                dynamic_risk = max(1000.0, dynamic_risk)  # Prudent floor

                # Position sizing: floor(dynamic_risk / (1.5 * ATR))
                raw_qty = int(math.floor(dynamic_risk / per_share_risk))

                # Capital Slot Cap: Max 20% of Portfolio Equity per slot, bounded by available cash
                max_slot_capital = current_portfolio_equity * MAX_SLOT_PCT
                max_qty_by_slot = int(math.floor(min(cash, max_slot_capital) / entry_price))
                qty = min(raw_qty, max_qty_by_slot)

                if qty >= 1:
                    total_cost = round(qty * entry_price, 2)
                    cash = round(cash - total_cost, 2)
                    available_slots -= 1
                    active_sectors.add(sector)  # Guard sector

                    initial_stop_loss = round(entry_price - per_share_risk, 2)
                    actual_trade_risk = round(qty * per_share_risk, 2)

                    new_pos = {
                        "id": trade_id_counter,
                        "symbol": sym,
                        "sector": sector,
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
        # Step 4: Record Daily Portfolio Valuation
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

    # Close any positions remaining open on the final date
    final_date = backtest_dates[-1]
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
        p["exit_reason"] = "Backtest Window Ended (Mark to Market)"
        closed_trades.append(p)

    df_trades = pd.DataFrame(closed_trades)
    df_equity = pd.DataFrame(equity_curve)

    # -------------------------------------------------------------
    # Performance & Net Tax Metrics Calculation
    # -------------------------------------------------------------
    start_equity = INITIAL_CAPITAL
    final_equity = df_equity["TotalEquity"].iloc[-1]
    total_net_pnl = final_equity - start_equity
    strategy_return_pct = (total_net_pnl / start_equity) * 100.0

    # Date duration & years for CAGR
    start_dt = pd.to_datetime(backtest_dates[0])
    end_dt = pd.to_datetime(backtest_dates[-1])
    years = (end_dt - start_dt).days / 365.25

    # Pre-Tax CAGR
    pre_tax_cagr = ((final_equity / start_equity) ** (1.0 / years) - 1.0) * 100.0

    # Nifty 50 Buy & Hold Return & CAGR
    nifty_start_close = nifty_dict[backtest_dates[0]]["Close"]
    nifty_end_close = nifty_dict[backtest_dates[-1]]["Close"]
    nifty_bh_return_pct = ((nifty_end_close - nifty_start_close) / nifty_start_close) * 100.0
    nifty_bh_cagr = ((nifty_end_close - nifty_start_close) / nifty_start_close + 1.0) ** (1.0 / years) - 1.0
    nifty_bh_cagr = nifty_bh_cagr * 100.0

    total_trades_count = len(df_trades)
    winning_trades = df_trades[df_trades["total_pnl"] > 0]
    losing_trades = df_trades[df_trades["total_pnl"] <= 0]
    win_count = len(winning_trades)
    loss_count = len(losing_trades)
    win_rate_pct = (win_count / total_trades_count * 100.0) if total_trades_count > 0 else 0.0

    gross_profit = winning_trades["total_pnl"].sum()
    gross_loss = abs(losing_trades["total_pnl"].sum())
    total_friction = df_trades["friction"].sum() if "friction" in df_trades.columns else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # Net Taxable Gain & STCG Tax Calculation
    # Net Taxable Gain = Gross Realized Profit - Gross Realized Loss (net profit after transaction friction)
    net_taxable_gain = max(0.0, total_net_pnl)
    stcg_tax_payable = STCG_TAX_RATE * net_taxable_gain

    post_tax_final_equity = final_equity - stcg_tax_payable
    post_tax_net_pnl = post_tax_final_equity - start_equity
    post_tax_total_return_pct = (post_tax_net_pnl / start_equity) * 100.0
    post_tax_cagr = ((post_tax_final_equity / start_equity) ** (1.0 / years) - 1.0) * 100.0

    # Max Drawdown Calculation
    df_equity["Peak"] = df_equity["TotalEquity"].cummax()
    df_equity["Drawdown"] = (df_equity["TotalEquity"] - df_equity["Peak"]) / df_equity["Peak"] * 100.0
    max_drawdown_pct = abs(df_equity["Drawdown"].min())

    avg_win = winning_trades["total_pnl"].mean() if win_count > 0 else 0.0
    avg_loss = losing_trades["total_pnl"].mean() if loss_count > 0 else 0.0
    avg_trade_pnl = df_trades["total_pnl"].mean() if total_trades_count > 0 else 0.0

    # Stage breakdowns
    stage_0_exits = len(df_trades[df_trades["stage"] == 0])
    stage_1_exits = len(df_trades[df_trades["stage"] == 1])
    stage_2_exits = len(df_trades[df_trades["stage"] == 2])

    metrics = {
        "start_date": backtest_dates[0],
        "end_date": backtest_dates[-1],
        "years": round(years, 2),
        "trading_sessions": len(backtest_dates),
        "initial_capital": start_equity,
        "final_equity": final_equity,
        "total_net_pnl": total_net_pnl,
        "strategy_return_pct": strategy_return_pct,
        "pre_tax_cagr": pre_tax_cagr,
        "post_tax_cagr": post_tax_cagr,
        "stcg_tax_payable": stcg_tax_payable,
        "post_tax_final_equity": post_tax_final_equity,
        "post_tax_net_pnl": post_tax_net_pnl,
        "post_tax_total_return_pct": post_tax_total_return_pct,
        "nifty_bh_return_pct": nifty_bh_return_pct,
        "nifty_bh_cagr": nifty_bh_cagr,
        "alpha_cagr": pre_tax_cagr - nifty_bh_cagr,
        "post_tax_alpha_cagr": post_tax_cagr - nifty_bh_cagr,
        "total_trades": total_trades_count,
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "win_rate_pct": win_rate_pct,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "total_friction": total_friction,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_trade_pnl": avg_trade_pnl,
        "stage_0_exits": stage_0_exits,
        "stage_1_exits": stage_1_exits,
        "stage_2_exits": stage_2_exits,
    }

    # Format and save backtest results CSV
    export_cols = [
        "id", "symbol", "sector", "entry_date", "entry_price", "qty", "cost",
        "trade_risk", "initial_stop_loss", "final_stop_loss", "highest_close", "max_r_reached",
        "stage", "exit_date", "exit_price", "gross_pnl", "friction", "total_pnl",
        "exit_reason", "relative_strength", "rsi"
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
    print("\n" + "=" * 84)
    print("      DYNAMIC COMPOUNDING & SECTOR-DIVERSIFIED SWING ENGINE: 3-YEAR REPORT")
    print("=" * 84)
    print(f"Backtest Period        : {m['start_date']}  to  {m['end_date']} ({m['trading_sessions']} sessions, {m['years']} yrs)")
    print(f"Screening Universe     : Nifty 200 Daily (Macro Trend + 20 EMA Pullback Confluence)")
    print(f"Sector Guard           : MAX 1 ACTIVE POSITION PER SECTOR (Ranked by 20d RS)")
    print(f"Position Sizing Model  : DYNAMIC COMPOUNDING (1.0% Equity Risk / Trade | 20% Slot Cap)")
    print(f"Trailing Stop Engine   : Stage 1 (+1.5R Breakeven) -> Stage 2 (+2.5R Chandelier 2.5x ATR)")
    print("-" * 84)
    print("CAPITAL & RETURN SCORECARD:")
    print(f"  Starting Capital             : Rs {m['initial_capital']:>15,.2f}")
    print(f"  Final Pre-Tax Equity         : Rs {m['final_equity']:>15,.2f}")
    print(f"  Pre-Tax Net Profit           : Rs {m['total_net_pnl']:>+15,.2f}")
    print(f"  Pre-Tax Total Return         :     {m['strategy_return_pct']:>14.2f} %")
    print(f"  Pre-Tax CAGR                 :     {m['pre_tax_cagr']:>14.2f} %")
    print(f"  Nifty 50 Buy & Hold CAGR     :     {m['nifty_bh_cagr']:>14.2f} % (Total: {m['nifty_bh_return_pct']:.2f}%)")
    print(f"  Pre-Tax Excess Alpha (CAGR)  :     {m['alpha_cagr']:>+14.2f} %")
    print("-" * 84)
    print("NET POST-TAX COMPOUNDING (20% STCG TAX & 0.15% FRICTION):")
    print(f"  Total Turnover Friction (0.15%): Rs {m['total_friction']:>13,.2f}")
    print(f"  STCG Tax Payable (20%)       : Rs {m['stcg_tax_payable']:>15,.2f}")
    print(f"  TOTAL NET POST-TAX EQUITY    : Rs {m['post_tax_final_equity']:>15,.2f}")
    print(f"  Post-Tax Net Profit          : Rs {m['post_tax_net_pnl']:>+15,.2f}")
    print(f"  Post-Tax Total Return        :     {m['post_tax_total_return_pct']:>14.2f} %")
    print(f"  POST-TAX CAGR                :     {m['post_tax_cagr']:>14.2f} %")
    print(f"  Post-Tax Excess Alpha (CAGR) :     {m['post_tax_alpha_cagr']:>+14.2f} %")
    print("-" * 84)
    print("RISK & EXECUTION METRICS:")
    print(f"  Total Trades Executed        :     {m['total_trades']:>14d}")
    print(f"  Winning Trades               :     {m['winning_trades']:>14d} ({m['win_rate_pct']:.1f}%)")
    print(f"  Losing Trades                :     {m['losing_trades']:>14d} ({100 - m['win_rate_pct']:.1f}%)")
    print(f"  Profit Factor                :     {m['profit_factor']:>14.2f}")
    print(f"  MAXIMUM STRATEGY DRAWDOWN (%):     {m['max_drawdown_pct']:>14.2f} %")
    print(f"  Average Winning Trade        : Rs {m['avg_win']:>+15,.2f}")
    print(f"  Average Losing Trade         : Rs {m['avg_loss']:>+15,.2f}")
    print(f"  Trade Expectancy             : Rs {m['avg_trade_pnl']:>+15,.2f}")
    print(f"  Exit Stages Breakdown        : Initial SL: {m['stage_0_exits']} | Breakeven: {m['stage_1_exits']} | Chandelier Trail: {m['stage_2_exits']}")
    print("=" * 84 + "\n")


if __name__ == "__main__":
    metrics, df_trades, df_equity = run_backtest()
    print_backtest_summary(metrics)
