import datetime
import math
import os
import sys
import urllib.request
from typing import List, Dict, Any, Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
import requests
import pandas as pd
import yfinance as yf
import ta

from database import (
    init_db,
    get_account,
    update_account,
    get_open_trades,
    record_trade,
    record_partial_exit,
    close_trade,
    get_all_trades,
)

# Capital & Risk Configuration
INITIAL_CAPITAL = 200000.0
MAX_OPEN_TRADES = 4
MAX_CAPITAL_PER_SLOT = INITIAL_CAPITAL / MAX_OPEN_TRADES  # Rs 50,000 per slot
RISK_PER_TRADE = 2000.0                                   # Rs 2,000 (1% of starting capital)
FRICTION_RATE = 0.0015                                    # 0.15% round-trip brokerage & friction

NIFTY_200_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
LOCAL_CSV_NAME = "nifty200.csv"


# Auto-load environment variables from .env if present
def load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

load_env_file()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8950347796").strip()

# Official NSE Trading Holidays
NSE_HOLIDAYS = {
    # 2024 Holidays
    "2024-01-22": "Special Holiday (Ayodhya Ram Mandir)",
    "2024-01-26": "Republic Day",
    "2024-03-08": "Mahashivratri",
    "2024-03-25": "Holi",
    "2024-03-29": "Good Friday",
    "2024-04-11": "Id-Ul-Fitr (Ramzan Id)",
    "2024-04-17": "Shri Ram Navami",
    "2024-05-01": "Maharashtra Day",
    "2024-05-20": "General Elections (Mumbai)",
    "2024-06-17": "Bakri Id",
    "2024-07-17": "Muharram",
    "2024-08-15": "Independence Day",
    "2024-10-02": "Mahatma Gandhi Jayanti",
    "2024-11-01": "Diwali Laxmi Pujan",
    "2024-11-15": "Gurunanak Jayanti",
    "2024-11-20": "Maharashtra Assembly Election",
    "2024-12-25": "Christmas",

    # 2025 Holidays
    "2025-01-26": "Republic Day",
    "2025-02-26": "Mahashivratri",
    "2025-03-14": "Holi",
    "2025-03-31": "Id-Ul-Fitr (Ramzan Id)",
    "2025-04-10": "Mahavir Jayanti",
    "2025-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day",
    "2025-06-07": "Bakri Id",
    "2025-07-06": "Muharram",
    "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi",
    "2025-10-02": "Mahatma Gandhi Jayanti",
    "2025-10-21": "Diwali Balipratipada",
    "2025-11-05": "Gurunanak Jayanti",
    "2025-12-25": "Christmas",

    # 2026 Holidays
    "2026-01-26": "Republic Day",
    "2026-02-16": "Mahashivratri",
    "2026-03-04": "Holi",
    "2026-03-20": "Id-Ul-Fitr (Ramzan Id)",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-27": "Bakri Id",
    "2026-06-25": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Gurunanak Jayanti",
    "2026-12-25": "Christmas",
}


def is_nse_holiday(dt: Optional[datetime.date] = None) -> bool:
    """
    Check if a given date (in Asia/Kolkata timezone) falls on a weekend or an official NSE holiday.
    Returns True if holiday/weekend, else False.
    """
    if dt is None:
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        dt = datetime.datetime.now(ist).date()

    # Weekend check: 5 = Saturday, 6 = Sunday
    if dt.weekday() in (5, 6):
        return True

    date_str = dt.strftime("%Y-%m-%d")
    month_day = dt.strftime("%m-%d")

    # Match against known holiday calendar
    if date_str in NSE_HOLIDAYS:
        return True

    # Annual fixed national holidays
    if month_day in {"01-26", "05-01", "08-15", "10-02", "12-25"}:
        return True

    return False


def send_telegram_alert(message: str) -> bool:
    """Send an alert message via Telegram Bot API using Markdown formatting."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN).strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID).strip()

    if not token:
        print("[INFO] Telegram alert skipped: TELEGRAM_BOT_TOKEN not configured.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[INFO] Telegram alert sent successfully.")
            return True
        else:
            print(f"[WARN] Failed to send Telegram alert: HTTP {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[WARN] Telegram request error: {e}")
        return False


def get_nifty50_regime(period: str = "1y") -> Dict[str, Any]:
    """
    Download daily candles for '^NSEI' (Nifty 50 benchmark index).
    Returns trend regime info:
        is_bullish: True if Close > 50-day EMA, else False.
        nifty_close: Latest closing price.
        nifty_ema50: 50-day EMA.
        nifty_20d_return: 20-day return fraction.
        nifty_date: Date string.
    """
    try:
        ticker = yf.Ticker("^NSEI")
        df = ticker.history(period=period, interval="1d")
        if df.empty or len(df) < 50:
            return {
                "is_bullish": True,
                "nifty_close": 0.0,
                "nifty_ema50": 0.0,
                "nifty_20d_return": 0.0,
                "nifty_date": "",
            }

        df = df.reset_index()
        date_col = "Date" if "Date" in df.columns else df.columns[0]
        df["Date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None)
        df = df.sort_values("Date").reset_index(drop=True)

        df["EMA_50"] = ta.trend.EMAIndicator(close=df["Close"], window=50).ema_indicator()

        latest = df.iloc[-1]
        nifty_close = float(latest["Close"])
        nifty_ema50 = float(latest["EMA_50"])
        nifty_date = str(latest["Date"])[:10]

        if len(df) >= 21:
            close_20_ago = float(df["Close"].iloc[-21])
            nifty_20d_return = (nifty_close - close_20_ago) / close_20_ago
        else:
            nifty_20d_return = 0.0

        is_bullish = nifty_close > nifty_ema50

        return {
            "is_bullish": is_bullish,
            "nifty_close": round(nifty_close, 2),
            "nifty_ema50": round(nifty_ema50, 2),
            "nifty_20d_return": nifty_20d_return,
            "nifty_date": nifty_date,
        }
    except Exception as e:
        print(f"[WARN] Error fetching Nifty 50 benchmark regime: {e}")
        return {
            "is_bullish": True,
            "nifty_close": 0.0,
            "nifty_ema50": 0.0,
            "nifty_20d_return": 0.0,
            "nifty_date": "",
        }


def load_nifty200_universe() -> List[str]:
    """Fetch official Nifty 200 constituent symbols with .NS suffix."""
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOCAL_CSV_NAME)

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        req = urllib.request.Request(NIFTY_200_CSV_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            content = resp.read()
            with open(csv_path, "wb") as f:
                f.write(content)
    except Exception:
        pass

    tickers = []
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            if "Symbol" in df.columns:
                tickers = [f"{str(s).strip()}.NS" for s in df["Symbol"] if pd.notna(s) and str(s).strip()]
        except Exception as e:
            print(f"[WARN] Error reading local CSV: {e}")

    if not tickers:
        tickers = [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
            "INFY.NS", "ITC.NS", "HINDUNILVR.NS", "LT.NS", "SBIN.NS"
        ]

    return tickers


def fetch_all_ticker_data(symbols: List[str], period: str = "1y") -> Dict[str, pd.DataFrame]:
    """Batch download daily historical data for all symbols."""
    results = {}
    if not symbols:
        return results

    try:
        data = yf.download(
            tickers=symbols,
            period=period,
            interval="1d",
            group_by="ticker",
            threads=True,
            progress=False
        )

        if isinstance(data.columns, pd.MultiIndex):
            has_level0 = any(s in data.columns.levels[0] for s in symbols)
            for sym in symbols:
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
                        results[sym] = df_sym
                except Exception:
                    pass
        else:
            if len(symbols) == 1:
                df_sym = data.dropna(subset=["Close"]).copy()
                if not df_sym.empty and len(df_sym) >= 50:
                    df_sym = df_sym.reset_index()
                    date_col = "Date" if "Date" in df_sym.columns else df_sym.columns[0]
                    df_sym["Date"] = pd.to_datetime(df_sym[date_col]).dt.tz_localize(None)
                    df_sym = df_sym.sort_values("Date").reset_index(drop=True)
                    results[symbols[0]] = df_sym
    except Exception as e:
        print(f"[WARN] Batch download encountered issue: {e}")

    return results


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate 10 EMA, 20 EMA, 50 EMA, 200 EMA, 14 RSI, 14 ATR, and 20 Volume SMA."""
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

    return df


def check_entry_setup(df: pd.DataFrame, nifty_20d_return: float) -> Optional[Dict[str, Any]]:
    """
    Evaluate Nifty 200 Technical Setup:
    1. Macro Trend: Close > 200 EMA and 20 EMA > 50 EMA.
    2. Pullback Dynamic: Daily Low <= 20 EMA, Daily Close > 20 EMA, and Close >= Open (bullish rejection).
    3. RSI Filter: 14-period RSI between 45 and 60 (healthy consolidation).
    4. Volume Expansion: Today's Volume >= 1.25 * 20-day Volume SMA.
    5. Relative Strength: 20-day Stock Return minus 20-day Nifty 50 Return.
    """
    if len(df) < 200:
        return None

    latest = df.iloc[-1]
    close = float(latest["Close"])
    open_p = float(latest["Open"])
    high = float(latest["High"])
    low = float(latest["Low"])
    vol = float(latest["Volume"])

    ema_10 = float(latest["EMA_10"])
    ema_20 = float(latest["EMA_20"])
    ema_50 = float(latest["EMA_50"])
    ema_200 = float(latest["EMA_200"])
    rsi = float(latest["RSI_14"])
    atr = float(latest["ATR_14"])
    vol_sma_20 = float(latest["VOL_SMA_20"])

    # Null / NaN check
    if any(pd.isna([close, open_p, high, low, vol, ema_10, ema_20, ema_50, ema_200, rsi, atr, vol_sma_20])):
        return None

    if vol_sma_20 <= 0:
        return None

    # Condition 1: Macro Trend (Close > 200 EMA and 20 EMA > 50 EMA)
    macro_trend_ok = (close > ema_200) and (ema_20 > ema_50)

    # Condition 2: Pullback Dynamic (Low <= 20 EMA, Close > 20 EMA, and Close >= Open)
    pullback_ok = (low <= ema_20) and (close > ema_20) and (close >= open_p)

    # Condition 3: RSI Filter (between 45 and 60)
    rsi_ok = (45.0 <= rsi <= 60.0)

    # Condition 4: Volume Expansion (Today's Volume >= 1.25 * 20-day Volume SMA)
    vol_ratio = vol / vol_sma_20
    vol_ok = (vol_ratio >= 1.25)

    if not (macro_trend_ok and pullback_ok and rsi_ok and vol_ok):
        return None

    # Calculate 20-day Return & Relative Strength vs Nifty 50
    if len(df) >= 21:
        close_20_ago = float(df["Close"].iloc[-21])
        stock_20d_return = (close - close_20_ago) / close_20_ago
    else:
        stock_20d_return = 0.0

    relative_strength = (stock_20d_return - nifty_20d_return) * 100.0

    # Volatility-Adjusted Risk (ATR) & Targets
    # Stop-Loss Price = Entry Price - (1.5 * ATR)
    per_share_risk = round(1.5 * atr, 2)
    if per_share_risk <= 0.05:
        return None

    stop_loss = round(close - per_share_risk, 2)
    # Target 1 (+2R): At +Rs 4,000 profit across trade / +3 * ATR gain
    target_1 = round(close + (2.0 * per_share_risk), 2)
    date_str = str(latest["Date"])[:10]

    return {
        "date": date_str,
        "close": round(close, 2),
        "open": round(open_p, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "ema_10": round(ema_10, 2),
        "ema_20": round(ema_20, 2),
        "ema_50": round(ema_50, 2),
        "ema_200": round(ema_200, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "vol_ratio": round(vol_ratio, 2),
        "relative_strength": round(relative_strength, 2),
        "stock_20d_return": round(stock_20d_return * 100, 2),
        "per_share_risk": per_share_risk,
        "stop_loss": stop_loss,
        "target_1": target_1,
    }


def monitor_open_trades() -> Dict[str, Any]:
    """
    Monitor existing open positions with hybrid exit execution:
    1. Phase 1 (OPEN):
       - If Low <= Stop Loss -> Exit 100% at SL (-1R).
       - If High >= Target 1 (+2R) -> Exit 50% to lock profit, move remaining 50% Stop to Breakeven (entry_price).
    2. Phase 2 (OPEN_RUNNER):
       - If Low <= Breakeven Stop Loss -> Exit remaining half at Breakeven.
       - If Daily Close < rising 10 EMA -> Exit remaining half at Close (Trailing Exit).
    Deducts 0.15% friction on all executed exits and sends instant Telegram alerts.
    """
    open_trades = get_open_trades()
    closed_summary = []
    partial_summary = []

    if not open_trades:
        return {"closed": closed_summary, "partial": partial_summary}

    acc = get_account()
    cash = acc["cash"]
    realized_pnl = acc["realized_pnl"]

    symbols = list({t["symbol"] for t in open_trades})
    stock_data = fetch_all_ticker_data(symbols, period="1y")

    for trade in open_trades:
        symbol = trade["symbol"]
        df = stock_data.get(symbol)
        if df is None or df.empty or len(df) < 20:
            continue

        df = calculate_indicators(df)
        latest = df.iloc[-1]
        high = float(latest["High"])
        low = float(latest["Low"])
        close = float(latest["Close"])
        ema_10 = float(latest["EMA_10"])
        date_str = str(latest["Date"])[:10]

        trade_id = trade["id"]
        entry_price = float(trade["entry_price"])
        stop_loss = float(trade["stop_loss"])
        target_1 = float(trade["target_1"] if trade["target_1"] else trade["target"])
        current_status = trade["status"]
        rem_qty = int(trade["remaining_qty"] if trade["remaining_qty"] else trade["qty"])

        # -------------------------------------------------------------
        # Case A: Status == 'OPEN' (Target 1 not yet reached)
        # -------------------------------------------------------------
        if current_status == "OPEN":
            # Check Stop Loss breach
            if low <= stop_loss:
                exit_price = stop_loss
                gross_pnl = (exit_price - entry_price) * rem_qty
                turnover = (entry_price + exit_price) * rem_qty
                friction = round(FRICTION_RATE * turnover, 2)
                net_pnl = round(gross_pnl - friction, 2)

                cash_returned = round((exit_price * rem_qty) - friction, 2)
                cash = round(cash + cash_returned, 2)
                realized_pnl = round(realized_pnl + net_pnl, 2)

                update_account(cash=cash, realized_pnl=realized_pnl)
                close_trade(
                    trade_id=trade_id,
                    exit_date=date_str,
                    exit_price=round(exit_price, 2),
                    pnl=net_pnl,
                    status="CLOSED_SL"
                )

                closed_summary.append({
                    "id": trade_id,
                    "symbol": symbol,
                    "exit_price": exit_price,
                    "qty": rem_qty,
                    "net_pnl": net_pnl,
                    "status": "CLOSED_SL",
                    "reason": "Initial Stop Loss Hit (-1R)",
                    "exit_date": date_str,
                })

                alert_msg = (
                    f"🛑 *STOP-LOSS HIT - TRADE CLOSED (-1R)*\n\n"
                    f"• *Symbol:* `{symbol}`\n"
                    f"• *Exit Price:* ₹{exit_price:,.2f} (SL: ₹{stop_loss:,.2f})\n"
                    f"• *Entry Price:* ₹{entry_price:,.2f}\n"
                    f"• *Quantity Exited:* {rem_qty}\n"
                    f"• *Net P&L:* ₹{net_pnl:+,.2f}\n"
                    f"• *Friction:* ₹{friction:,.2f}\n"
                    f"• *Date:* {date_str}"
                )
                send_telegram_alert(alert_msg)

            # Check Target 1 (+2R) reached
            elif high >= target_1:
                # 50% partial exit
                qty_exit = max(1, rem_qty // 2) if rem_qty > 1 else 1
                qty_runner = rem_qty - qty_exit
                exit_price = target_1

                gross_pnl = (exit_price - entry_price) * qty_exit
                turnover = (entry_price + exit_price) * qty_exit
                friction = round(FRICTION_RATE * turnover, 2)
                t1_net_pnl = round(gross_pnl - friction, 2)

                cash_returned = round((exit_price * qty_exit) - friction, 2)
                cash = round(cash + cash_returned, 2)
                realized_pnl = round(realized_pnl + t1_net_pnl, 2)
                update_account(cash=cash, realized_pnl=realized_pnl)

                if qty_runner > 0:
                    # Move stop loss to breakeven (entry_price) for remaining half
                    new_stop_loss = entry_price
                    record_partial_exit(
                        trade_id=trade_id,
                        t1_exit_date=date_str,
                        t1_exit_price=round(exit_price, 2),
                        t1_pnl=t1_net_pnl,
                        remaining_qty=qty_runner,
                        new_stop_loss=new_stop_loss
                    )

                    partial_summary.append({
                        "id": trade_id,
                        "symbol": symbol,
                        "exit_price": exit_price,
                        "qty_exited": qty_exit,
                        "qty_runner": qty_runner,
                        "t1_pnl": t1_net_pnl,
                        "new_stop_loss": new_stop_loss,
                        "date": date_str,
                    })

                    alert_msg = (
                        f"🎯 *TARGET 1 REACHED (+2R) - 50% PROFIT LOCKED IN*\n\n"
                        f"• *Symbol:* `{symbol}`\n"
                        f"• *Target 1 Price:* ₹{exit_price:,.2f}\n"
                        f"• *Quantity Exited (50%):* {qty_exit} shares\n"
                        f"• *Locked-in Profit:* ₹{t1_net_pnl:+,.2f}\n"
                        f"• *Remaining Runner:* {qty_runner} shares\n"
                        f"• *New Stop-Loss:* ₹{new_stop_loss:,.2f} (*Breakeven*)\n"
                        f"• *Trail Strategy:* Trailing via daily close < 10 EMA\n"
                        f"• *Date:* {date_str}"
                    )
                    send_telegram_alert(alert_msg)
                else:
                    close_trade(
                        trade_id=trade_id,
                        exit_date=date_str,
                        exit_price=round(exit_price, 2),
                        pnl=t1_net_pnl,
                        status="CLOSED_TARGET"
                    )

                    closed_summary.append({
                        "id": trade_id,
                        "symbol": symbol,
                        "exit_price": exit_price,
                        "qty": qty_exit,
                        "net_pnl": t1_net_pnl,
                        "status": "CLOSED_TARGET",
                        "reason": "Target 1 Hit (Full position closed)",
                        "exit_date": date_str,
                    })

        # -------------------------------------------------------------
        # Case B: Status == 'OPEN_RUNNER' (Target 1 secured, trailing runner)
        # -------------------------------------------------------------
        elif current_status == "OPEN_RUNNER":
            breakeven_sl = stop_loss  # Already set to entry_price

            # Stop out at breakeven
            if low <= breakeven_sl:
                exit_price = breakeven_sl
                runner_gross_pnl = (exit_price - entry_price) * rem_qty
                turnover = (entry_price + exit_price) * rem_qty
                friction = round(FRICTION_RATE * turnover, 2)
                runner_net_pnl = round(runner_gross_pnl - friction, 2)
                total_pnl = round(float(trade["t1_pnl"] or 0.0) + runner_net_pnl, 2)

                cash_returned = round((exit_price * rem_qty) - friction, 2)
                cash = round(cash + cash_returned, 2)
                realized_pnl = round(realized_pnl + runner_net_pnl, 2)

                update_account(cash=cash, realized_pnl=realized_pnl)
                close_trade(
                    trade_id=trade_id,
                    exit_date=date_str,
                    exit_price=round(exit_price, 2),
                    pnl=total_pnl,
                    status="CLOSED_TRAIL"
                )

                closed_summary.append({
                    "id": trade_id,
                    "symbol": symbol,
                    "exit_price": exit_price,
                    "qty": rem_qty,
                    "net_pnl": total_pnl,
                    "status": "CLOSED_TRAIL",
                    "reason": "Runner Breakeven Stop Triggered",
                    "exit_date": date_str,
                })

                alert_msg = (
                    f"🛡️ *RUNNER STOPPED OUT AT BREAKEVEN*\n\n"
                    f"• *Symbol:* `{symbol}`\n"
                    f"• *Exit Price:* ₹{exit_price:,.2f} (Breakeven)\n"
                    f"• *Runner Quantity:* {rem_qty}\n"
                    f"• *Runner P&L:* ₹{runner_net_pnl:+,.2f} (Friction: ₹{friction:,.2f})\n"
                    f"• *Total Trade Net P&L:* ₹{total_pnl:+,.2f}\n"
                    f"• *Date:* {date_str}"
                )
                send_telegram_alert(alert_msg)

            # Trailing exit: Daily close below 10-day EMA
            elif close < ema_10:
                exit_price = close
                runner_gross_pnl = (exit_price - entry_price) * rem_qty
                turnover = (entry_price + exit_price) * rem_qty
                friction = round(FRICTION_RATE * turnover, 2)
                runner_net_pnl = round(runner_gross_pnl - friction, 2)
                total_pnl = round(float(trade["t1_pnl"] or 0.0) + runner_net_pnl, 2)

                cash_returned = round((exit_price * rem_qty) - friction, 2)
                cash = round(cash + cash_returned, 2)
                realized_pnl = round(realized_pnl + runner_net_pnl, 2)

                update_account(cash=cash, realized_pnl=realized_pnl)
                close_trade(
                    trade_id=trade_id,
                    exit_date=date_str,
                    exit_price=round(exit_price, 2),
                    pnl=total_pnl,
                    status="CLOSED_TRAIL"
                )

                closed_summary.append({
                    "id": trade_id,
                    "symbol": symbol,
                    "exit_price": exit_price,
                    "qty": rem_qty,
                    "net_pnl": total_pnl,
                    "status": "CLOSED_TRAIL",
                    "reason": f"Runner Trailing Exit: Close (₹{close}) < 10 EMA (₹{round(ema_10, 2)})",
                    "exit_date": date_str,
                })

                alert_msg = (
                    f"🏃 *RUNNER TRAIL EXIT (CLOSE < 10 EMA)*\n\n"
                    f"• *Symbol:* `{symbol}`\n"
                    f"• *Exit Price:* ₹{exit_price:,.2f}\n"
                    f"• *10-day EMA:* ₹{ema_10:,.2f}\n"
                    f"• *Runner Quantity:* {rem_qty}\n"
                    f"• *Runner Profit:* ₹{runner_net_pnl:+,.2f}\n"
                    f"• *Total Trade Net P&L:* ₹{total_pnl:+,.2f}\n"
                    f"• *Date:* {date_str}"
                )
                send_telegram_alert(alert_msg)

    return {"closed": closed_summary, "partial": partial_summary}


def scan_and_enter_trades(regime: Dict[str, Any]) -> Dict[str, Any]:
    """
    Screen Nifty 200 universe with Relative Strength ranking and Volatility-adjusted sizing:
    - Only allowed if Nifty 50 Close > 50-day EMA.
    - Ranks qualifying setups by 20-day Relative Strength.
    - Takes top setups up to available slots (max 4 concurrent positions).
    - Sizes positions: floor(2000 / Per-Share Risk), bounded by cash & equal capital slots (Rs 50,000).
    """
    acc = get_account()
    cash = acc["cash"]
    realized_pnl = acc["realized_pnl"]

    open_trades = get_open_trades()
    open_symbols = {t["symbol"] for t in open_trades}
    available_slots = MAX_OPEN_TRADES - len(open_trades)

    scans_evaluated = 0
    signals_found = []
    executed_trades = []

    universe = load_nifty200_universe()

    # Benchmark Regime Check
    if not regime["is_bullish"]:
        print(
            f"\n[REGIME] Nifty 50 Close (Rs {regime['nifty_close']}) <= 50 EMA (Rs {regime['nifty_ema50']}). "
            f"Market regime is DEFENSIVE. New trade screening suspended."
        )
        return {
            "universe_size": len(universe),
            "scans_evaluated": 0,
            "signals_found": [],
            "executed_trades": [],
            "regime": regime,
            "suspended": True,
        }

    print(f"\n[SCAN] Batch downloading 1-year data for {len(universe)} Nifty 200 tickers...")
    print(f"[SCAN] Available Open Slots: {available_slots}/{MAX_OPEN_TRADES}")

    data_map = fetch_all_ticker_data(universe, period="1y")

    for symbol in universe:
        df = data_map.get(symbol)
        if df is None or len(df) < 200:
            continue

        scans_evaluated += 1
        df = calculate_indicators(df)
        setup = check_entry_setup(df, regime["nifty_20d_return"])

        if setup:
            setup["symbol"] = symbol
            signals_found.append(setup)

    # Rank qualifying candidates by 20-day Relative Strength (highest RS first)
    signals_found.sort(key=lambda s: s["relative_strength"], reverse=True)

    # Execute entries for top candidates if slots and cash are available
    for setup in signals_found:
        symbol = setup["symbol"]

        if available_slots <= 0:
            break

        if symbol in open_symbols:
            continue

        per_share_risk = setup["per_share_risk"]
        close_price = setup["close"]

        # Risk-adjusted sizing: floor(2000 / Per-Share Risk)
        raw_qty = int(math.floor(RISK_PER_TRADE / per_share_risk))

        # Capital slot bounding: Rs 50,000 max per slot and available cash
        max_qty_by_slot = int(math.floor(min(cash, MAX_CAPITAL_PER_SLOT) / close_price))
        qty = min(raw_qty, max_qty_by_slot)

        if qty >= 1:
            total_cost = round(qty * close_price, 2)
            cash = round(cash - total_cost, 2)

            trade_id = record_trade(
                symbol=symbol,
                entry_date=setup["date"],
                entry_price=close_price,
                qty=qty,
                stop_loss=setup["stop_loss"],
                target_1=setup["target_1"]
            )

            update_account(cash=cash, realized_pnl=realized_pnl)
            open_symbols.add(symbol)
            available_slots -= 1

            trade_info = {
                "id": trade_id,
                "symbol": symbol,
                "entry_date": setup["date"],
                "entry_price": close_price,
                "qty": qty,
                "cost": total_cost,
                "stop_loss": setup["stop_loss"],
                "target_1": setup["target_1"],
                "atr": setup["atr"],
                "per_share_risk": per_share_risk,
                "relative_strength": setup["relative_strength"],
                "rsi": setup["rsi"],
                "vol_ratio": setup["vol_ratio"],
            }
            executed_trades.append(trade_info)

            # Instant Telegram Alert for New Trade Entered
            trade_alert = (
                f"🚀 *NEW SWING TRADE ENTERED (VOLATILITY ADJUSTED)*\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Entry Price:* ₹{close_price:,.2f}\n"
                f"• *Quantity:* {qty} shares (Allocation: ₹{total_cost:,.2f})\n"
                f"• *Stop-Loss (1.5x ATR):* ₹{setup['stop_loss']:,.2f}\n"
                f"• *Target 1 (+2R / 50% Lock):* ₹{setup['target_1']:,.2f}\n"
                f"• *Per-Share Risk:* ₹{per_share_risk:,.2f} (Total: ₹{round(per_share_risk * qty, 2):,.2f})\n"
                f"• *Relative Strength vs Nifty:* {setup['relative_strength']:+.2f}%\n"
                f"• *RSI:* {setup['rsi']} | *Volume Ratio:* {setup['vol_ratio']}x\n"
                f"• *Date:* {setup['date']}"
            )
            send_telegram_alert(trade_alert)

    return {
        "universe_size": len(universe),
        "scans_evaluated": scans_evaluated,
        "signals_found": signals_found,
        "executed_trades": executed_trades,
        "regime": regime,
        "suspended": False,
    }


def send_eod_summary_alert(
    scan_result: Dict[str, Any],
    monitoring_result: Dict[str, Any]
) -> None:
    """Send comprehensive daily EOD Telegram alert with regime, positions, and signals."""
    acc = get_account()
    open_trades = get_open_trades()
    regime = scan_result.get("regime", {})

    open_equity = sum(ot["entry_price"] * (ot["remaining_qty"] if ot["remaining_qty"] else ot["qty"]) for ot in open_trades)
    total_equity = acc["cash"] + open_equity

    regime_status = "🟢 BULLISH (Close > 50 EMA)" if regime.get("is_bullish") else "🔴 DEFENSIVE (Close <= 50 EMA)"
    nifty_close = regime.get("nifty_close", 0.0)
    nifty_ema50 = regime.get("nifty_ema50", 0.0)
    nifty_ret20 = regime.get("nifty_20d_return", 0.0) * 100

    signals = scan_result.get("signals_found", [])
    executed = scan_result.get("executed_trades", [])
    closed = monitoring_result.get("closed", [])
    partial = monitoring_result.get("partial", [])

    lines = [
        "📊 *SWING ENGINE - EOD SUMMARY (NIFTY 200)*",
        f"_{datetime.datetime.now().strftime('%d %b %Y | %I:%M %p IST')}_\n",
        f"🌐 *Benchmark Regime (Nifty 50):*",
        f"• *Regime:* {regime_status}",
        f"• *Nifty 50 Close:* ₹{nifty_close:,.2f} | *50 EMA:* ₹{nifty_ema50:,.2f}",
        f"• *20-Day Return:* {nifty_ret20:+.2f}%\n",
        f"📌 *Active Positions ({len(open_trades)}/{MAX_OPEN_TRADES}):*"
    ]

    if open_trades:
        for ot in open_trades:
            sym = ot["symbol"]
            qty = ot["remaining_qty"] if ot["remaining_qty"] else ot["qty"]
            ep = ot["entry_price"]
            sl = ot["stop_loss"]
            t1 = ot["target_1"] if ot["target_1"] else ot["target"]
            st = ot["status"]

            if st == "OPEN_RUNNER":
                lines.append(f"  • *{sym}:* {qty} shares @ ₹{ep:,.2f} | *RUNNER* (BE SL: ₹{sl:,.2f} | Trail: 10 EMA)")
            else:
                lines.append(f"  • *{sym}:* {qty} shares @ ₹{ep:,.2f} | SL: ₹{sl:,.2f} | T1: ₹{t1:,.2f}")
    else:
        lines.append("  • No active positions")

    lines.append(f"\n⚡ *Signals Today ({len(signals)} Qualifying, Ranked by RS):*")
    if signals:
        for s in signals[:5]:  # Top 5 by RS
            lines.append(
                f"  • *{s['symbol']}:* RS: {s['relative_strength']:+.1f}% | RSI: {s['rsi']} | "
                f"Vol: {s['vol_ratio']}x | SL: ₹{s['stop_loss']:,.2f} | T1: ₹{s['target_1']:,.2f}"
            )
        if len(signals) > 5:
            lines.append(f"    _(and {len(signals) - 5} more qualifying setups)_")
    else:
        lines.append("  • None qualifying today")

    if executed:
        lines.append(f"\n🚀 *New Trades Opened:* {len(executed)}")
        for et in executed:
            lines.append(f"  • {et['symbol']} ({et['qty']} shares @ ₹{et['entry_price']})")

    if partial:
        lines.append(f"\n🎯 *Target 1 Locks (50%):* {len(partial)}")
        for pt in partial:
            lines.append(f"  • {pt['symbol']} (Exited {pt['qty_exited']} shares @ ₹{pt['exit_price']} | Profit: ₹{pt['t1_pnl']:+,.2f})")

    if closed:
        lines.append(f"\n🚪 *Trades Closed:* {len(closed)}")
        for ct in closed:
            lines.append(f"  • {ct['symbol']} ({ct['status']} @ ₹{ct['exit_price']} | P&L: ₹{ct['net_pnl']:+,.2f})")

    lines.extend([
        f"\n💼 *Portfolio & Capital:*",
        f"• *Available Cash:* ₹{acc['cash']:,.2f}",
        f"• *Realized P&L:* ₹{acc['realized_pnl']:+,.2f}",
        f"• *Open Equity (Cost):* ₹{open_equity:,.2f}",
        f"• *Total Account Value:* ₹{total_equity:,.2f}"
    ])

    full_message = "\n".join(lines)
    send_telegram_alert(full_message)


def print_summary(
    scan_result: Dict[str, Any],
    monitoring_result: Dict[str, Any]
) -> None:
    """Print clean structured summary to console."""
    acc = get_account()
    open_trades = get_open_trades()
    regime = scan_result.get("regime", {})

    print("\n" + "=" * 80)
    print("      HIGH-EXPECTANCY VOLATILITY-ADJUSTED SWING ENGINE (NIFTY 200)")
    print("=" * 80)
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    regime_label = "[BULLISH]" if regime.get("is_bullish") else "[DEFENSIVE]"
    print(
        f"Benchmark Regime: {regime_label} "
        f"(Nifty 50: Rs {regime.get('nifty_close', 0.0):,.2f} | 50 EMA: Rs {regime.get('nifty_ema50', 0.0):,.2f} | "
        f"20d Ret: {regime.get('nifty_20d_return', 0.0)*100:+.2f}%)"
    )

    if scan_result.get("suspended"):
        print("[NOTICE] Nifty 50 below 50 EMA -> New trade entry screening SUSPENDED for defense.")
    else:
        print(f"Universe Evaluated: {scan_result['scans_evaluated']}/{scan_result['universe_size']} tickers")
        print(f"Setups Qualifying Today: {len(scan_result['signals_found'])}")

    if scan_result.get("signals_found"):
        print("\n--- Qualifying Signals (Ranked by Relative Strength) ---")
        for s in scan_result["signals_found"][:8]:
            print(
                f"  * {s['symbol']:<15} | RS: {s['relative_strength']:+6.2f}% | RSI: {s['rsi']:4.1f} | "
                f"Vol: {s['vol_ratio']:4.2f}x | Close: Rs {s['close']:<8} | SL: Rs {s['stop_loss']:<8} | T1: Rs {s['target_1']:<8}"
            )

    print("\n--- Position Exits / Management Today ---")
    closed = monitoring_result.get("closed", [])
    partial = monitoring_result.get("partial", [])
    if partial:
        for p in partial:
            print(f"  * [TARGET 1] #{p['id']} {p['symbol']} exited 50% ({p['qty_exited']} shs) @ Rs {p['exit_price']} | Locked: Rs {p['t1_pnl']} | Runner SL: Rs {p['new_stop_loss']}")
    if closed:
        for c in closed:
            print(f"  * [{c['status']}] #{c['id']} {c['symbol']} exited @ Rs {c['exit_price']} ({c['reason']}) | Net PnL: Rs {c['net_pnl']}")
    if not partial and not closed:
        print("  None (No exit triggers breached today)")

    print("\n--- New Trades Executed Today ---")
    executed = scan_result.get("executed_trades", [])
    if executed:
        for et in executed:
            print(
                f"  * [BUY] {et['symbol']:<15} Qty: {et['qty']:<4} @ Rs {et['entry_price']:<8} | "
                f"Alloc: Rs {et['cost']:<8} | SL: Rs {et['stop_loss']} | T1: Rs {et['target_1']} | RS: {et['relative_strength']:+.1f}%"
            )
    else:
        print("  None (Slots full, no qualifying signals, or cash constrained)")

    print(f"\n--- Current Open Positions ({len(open_trades)}/{MAX_OPEN_TRADES}) ---")
    if open_trades:
        for ot in open_trades:
            sym = ot["symbol"]
            qty = ot["remaining_qty"] if ot["remaining_qty"] else ot["qty"]
            st = ot["status"]
            sl = ot["stop_loss"]
            t1 = ot["target_1"] if ot["target_1"] else ot["target"]
            if st == "OPEN_RUNNER":
                print(f"  * #{ot['id']} {sym:<15} [RUNNER] Qty: {qty:<4} Entry: Rs {ot['entry_price']:<8} BE SL: Rs {sl:<8} Trail: 10 EMA")
            else:
                print(f"  * #{ot['id']} {sym:<15} [OPEN]   Qty: {qty:<4} Entry: Rs {ot['entry_price']:<8} SL: Rs {sl:<8} T1: Rs {t1:<8}")
    else:
        print("  No open positions")

    print("\n--- Account Overview ---")
    open_equity = sum(ot["entry_price"] * (ot["remaining_qty"] if ot["remaining_qty"] else ot["qty"]) for ot in open_trades)
    total_val = acc["cash"] + open_equity
    print(f"  Initial Capital : Rs {acc['initial_capital']:,.2f}")
    print(f"  Available Cash  : Rs {acc['cash']:,.2f}")
    print(f"  Realized PnL    : Rs {acc['realized_pnl']:,.2f}")
    print(f"  Open Positions  : Rs {open_equity:,.2f} (cost)")
    print(f"  Estimated Equity: Rs {total_val:,.2f}")
    print("=" * 80 + "\n")


def run(force: bool = False):
    """Main execution cycle."""
    if not force and is_nse_holiday():
        print("[INFO] Today is an NSE market holiday or weekend. Skipping scan and silencing alerts.")
        sys.exit(0)

    init_db()

    print("[INIT] Checking Nifty 50 Benchmark Trend Regime...")
    regime = get_nifty50_regime()

    print("[INIT] Monitoring open positions with hybrid exits & trailing stops...")
    monitoring_result = monitor_open_trades()

    print("[INIT] Scanning Nifty 200 universe...")
    scan_result = scan_and_enter_trades(regime)

    print_summary(scan_result, monitoring_result)

    # Send EOD summary alert to Telegram
    send_eod_summary_alert(scan_result, monitoring_result)


if __name__ == "__main__":
    run()
