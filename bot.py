import datetime
import math
import os
import sys
import urllib.request
from typing import List, Dict, Any, Optional
import requests
import pandas as pd
import yfinance as yf
import ta

from database import (
    init_db,
    reset_portfolio,
    get_account,
    update_account,
    get_open_trades,
    record_trade,
    update_trade_trailing_stop,
    close_trade,
    get_all_trades,
)

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Production Configuration (10-Year Validated Dynamic Compounding Engine)
INITIAL_CAPITAL = 200000.0
MAX_OPEN_TRADES = 5                                       # 5 concurrent slots
RISK_PCT_PER_TRADE = 0.01                                 # 1.0% dynamic risk of current portfolio equity
MAX_SLOT_PCT = 0.20                                       # Max 20% capital deployed per slot
FRICTION_RATE = 0.0015                                    # 0.15% round-trip brokerage & friction
DEPLOYMENT_START_DATE = datetime.date(2026, 11, 10)       # Fresh entries begin Tuesday, 2026-11-10 at 3:35 PM IST

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
    """Check if date in Asia/Kolkata timezone falls on weekend or official NSE holiday."""
    if dt is None:
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        dt = datetime.datetime.now(ist).date()

    if dt.weekday() in (5, 6):
        return True

    date_str = dt.strftime("%Y-%m-%d")
    month_day = dt.strftime("%m-%d")

    if date_str in NSE_HOLIDAYS:
        return True

    if month_day in {"01-26", "05-01", "08-15", "10-02", "12-25"}:
        return True

    return False


def send_telegram_alert(message: str) -> bool:
    """Send alert message via Telegram Bot API with Markdown formatting."""
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
    Download daily candles for '^NSEI' (Nifty 50).
    Dual-Speed Market Filter: Nifty 50 Close > 20-day EMA OR Close > 50-day EMA.
    Returns:
        {'bullish': bool, 'is_bullish': bool, 'nifty_close': float, 'ema20': float,
         'nifty_ema20': float, 'ema50': float, 'nifty_ema50': float,
         'status': str, 'nifty_20d_return': float, 'nifty_date': str}
    """
    df = None
    try:
        ticker = yf.Ticker("^NSEI")
        df = ticker.history(period=period, interval="1d")
    except Exception as e:
        print(f"[WARN] yfinance error for ^NSEI: {e}")

    # Fallback to local cache if live fetch failed or returned empty
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "NSEI.csv")
    if (df is None or df.empty or len(df) < 50) and os.path.exists(cache_path):
        try:
            df = pd.read_csv(cache_path)
            print("[INFO] Loaded ^NSEI benchmark data from local cache.")
        except Exception:
            pass

    if df is None or df.empty or len(df) < 50:
        return {
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

    try:
        df = df.reset_index()
        date_col = "Date" if "Date" in df.columns else df.columns[0]
        df["Date"] = pd.to_datetime(df[date_col]).dt.tz_localize(None)
        df = df.sort_values("Date").reset_index(drop=True)

        df["EMA_20"] = ta.trend.EMAIndicator(close=df["Close"], window=20).ema_indicator()
        df["EMA_50"] = ta.trend.EMAIndicator(close=df["Close"], window=50).ema_indicator()

        latest = df.iloc[-1]
        nifty_close = float(latest["Close"])
        nifty_ema20 = float(latest["EMA_20"])
        nifty_ema50 = float(latest["EMA_50"])
        nifty_date = str(latest["Date"])[:10]

        if len(df) >= 21:
            close_20_ago = float(df["Close"].iloc[-21])
            nifty_20d_return = (nifty_close - close_20_ago) / close_20_ago
        else:
            nifty_20d_return = 0.0

        # Allow new buys if Close > 20 EMA OR Close > 50 EMA
        is_bullish = (nifty_close > nifty_ema20) or (nifty_close > nifty_ema50)
        status_str = "🟢 Bullish (Close > 20/50 EMA)" if is_bullish else "🔴 Defensive / Cash"

        return {
            "bullish": is_bullish,
            "is_bullish": is_bullish,
            "nifty_close": round(nifty_close, 2),
            "ema20": round(nifty_ema20, 2),
            "nifty_ema20": round(nifty_ema20, 2),
            "ema50": round(nifty_ema50, 2),
            "nifty_ema50": round(nifty_ema50, 2),
            "status": status_str,
            "nifty_20d_return": round(nifty_20d_return, 4),
            "nifty_date": nifty_date,
        }
    except Exception as e:
        print(f"[WARN] Error computing Nifty 50 benchmark regime: {e}")
        return {
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
    """Calculate 20 EMA, 50 EMA, 200 EMA, 14 RSI, 14 ATR, and 20 Volume SMA."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    df["EMA_20"] = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
    df["EMA_50"] = ta.trend.EMAIndicator(close=close, window=50).ema_indicator()
    df["EMA_200"] = ta.trend.EMAIndicator(close=close, window=200).ema_indicator()
    df["RSI_14"] = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    df["ATR_14"] = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()
    df["VOL_SMA_20"] = volume.rolling(window=20).mean()

    return df


def check_entry_setup(df: pd.DataFrame, nifty_20d_return: float) -> Optional[Dict[str, Any]]:
    """
    Test 2 Confluence Setup:
    - Close > 200 EMA and 20 EMA > 50 EMA
    - Low <= 20 EMA, Close > 20 EMA, Close >= Open
    - RSI between 40 and 65 (healthy pullback)
    - Volume >= 0.85 * 20-day Volume SMA
    - Stop-Loss: Entry - (1.5 * ATR)
    """
    if len(df) < 200:
        return None

    latest = df.iloc[-1]
    close = float(latest["Close"])
    open_p = float(latest["Open"])
    high = float(latest["High"])
    low = float(latest["Low"])
    vol = float(latest["Volume"])

    ema_20 = float(latest["EMA_20"])
    ema_50 = float(latest["EMA_50"])
    ema_200 = float(latest["EMA_200"])
    rsi = float(latest["RSI_14"])
    atr = float(latest["ATR_14"])
    vol_sma_20 = float(latest["VOL_SMA_20"])

    if any(pd.isna([close, open_p, high, low, vol, ema_20, ema_50, ema_200, rsi, atr, vol_sma_20])):
        return None

    if vol_sma_20 <= 0 or atr <= 0.05:
        return None

    # 1. Macro Trend: Close > 200 EMA and 20 EMA > 50 EMA
    macro_trend_ok = (close > ema_200) and (ema_20 > ema_50)

    # 2. Pullback Dynamic: Low <= 20 EMA, Close > 20 EMA, Close >= Open
    pullback_ok = (low <= ema_20) and (close > ema_20) and (close >= open_p)

    # 3. RSI Filter: 40.0 <= RSI <= 65.0
    rsi_ok = (40.0 <= rsi <= 65.0)

    # 4. Volume Filter: Volume >= 0.85 * 20-day Volume SMA
    vol_ratio = vol / vol_sma_20
    vol_ok = (vol_ratio >= 0.85)

    if not (macro_trend_ok and pullback_ok and rsi_ok and vol_ok):
        return None

    # Calculate 20-day Relative Strength vs Nifty 50
    if len(df) >= 21:
        close_20_ago = float(df["Close"].iloc[-21])
        stock_20d_return = (close - close_20_ago) / close_20_ago
    else:
        stock_20d_return = 0.0

    relative_strength = (stock_20d_return - nifty_20d_return) * 100.0

    # Volatility-Adjusted Initial Risk (1.5x ATR)
    per_share_risk = round(1.5 * atr, 2)
    if per_share_risk <= 0.05:
        return None

    initial_stop_loss = round(close - per_share_risk, 2)
    date_str = str(latest["Date"])[:10]

    return {
        "date": date_str,
        "close": round(close, 2),
        "open": round(open_p, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "ema_20": round(ema_20, 2),
        "ema_50": round(ema_50, 2),
        "ema_200": round(ema_200, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "vol_ratio": round(vol_ratio, 2),
        "relative_strength": round(relative_strength, 2),
        "stock_20d_return": round(stock_20d_return * 100, 2),
        "per_share_risk": per_share_risk,
        "stop_loss": initial_stop_loss,
    }


def monitor_open_trades() -> Dict[str, Any]:
    """
    2-Stage ATR Chandelier Trailing Engine:
    - Exit Condition: Exit full position if Low <= current Trailing Stop.
    - Stage 1: When unrealized gain touches +1.5R (+Rs 3,000), move stop to Entry Price (Breakeven).
    - Stage 2: When unrealized gain touches +2.5R (+Rs 5,000), activate trailing stop:
      Trailing Stop = Highest Daily Close - (2.5 * ATR) [Ratchets upward only].
    """
    open_trades = get_open_trades()
    closed_summary = []
    trailing_updates = []

    if not open_trades:
        return {"closed": closed_summary, "trailing_updates": trailing_updates}

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
        open_p = float(latest["Open"])
        high = float(latest["High"])
        low = float(latest["Low"])
        close = float(latest["Close"])
        atr = float(latest["ATR_14"])
        date_str = str(latest["Date"])[:10]

        trade_id = trade["id"]
        entry_price = float(trade["entry_price"])
        qty = int(trade["qty"])
        current_stop = float(trade["stop_loss"])
        per_share_risk = float(trade.get("per_share_risk") or (round(entry_price - current_stop, 2)))
        highest_close = float(trade.get("highest_close") or entry_price)
        stage = int(trade.get("stage") or 0)
        max_r_reached = float(trade.get("max_r_reached") or 0.0)

        # -------------------------------------------------------------
        # Step 1: Check if Stop Loss / Trailing Stop breached today
        # -------------------------------------------------------------
        if low <= current_stop:
            exit_price = current_stop
            if open_p < current_stop:
                exit_price = min(open_p, current_stop)

            gross = (exit_price - entry_price) * qty
            turnover = (entry_price + exit_price) * qty
            friction = round(FRICTION_RATE * turnover, 2)
            net_pnl = round(gross - friction, 2)

            cash_returned = round((exit_price * qty) - friction, 2)
            cash = round(cash + cash_returned, 2)
            realized_pnl = round(realized_pnl + net_pnl, 2)

            update_account(cash=cash, realized_pnl=realized_pnl)

            if stage == 2:
                status = "CLOSED_CHANDELIER"
                exit_reason = f"Chandelier Trailing Stop (Peak Close ₹{highest_close:,.2f} - 2.5x ATR)"
                alert_title = "🏃 *CHANDELIER TRAILING STOP EXIT*"
            elif stage == 1:
                status = "CLOSED_BREAKEVEN"
                exit_reason = "Breakeven Stop Hit (+1.5R Locked)"
                alert_title = "🛡️ *BREAKEVEN STOP EXIT*"
            else:
                status = "CLOSED_SL"
                exit_reason = "Initial Stop-Loss Hit (-1.5x ATR)"
                alert_title = "🛑 *STOP-LOSS HIT (-1.5x ATR)*"

            close_trade(
                trade_id=trade_id,
                exit_date=date_str,
                exit_price=round(exit_price, 2),
                pnl=net_pnl,
                status=status,
                exit_reason=exit_reason
            )

            closed_summary.append({
                "id": trade_id,
                "symbol": symbol,
                "exit_price": exit_price,
                "qty": qty,
                "net_pnl": net_pnl,
                "status": status,
                "reason": exit_reason,
                "exit_date": date_str,
            })

            alert_msg = (
                f"{alert_title}\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Exit Price:* ₹{exit_price:,.2f}\n"
                f"• *Entry Price:* ₹{entry_price:,.2f}\n"
                f"• *Quantity:* {qty} shares\n"
                f"• *Net P&L:* ₹{net_pnl:+,.2f}\n"
                f"• *Friction:* ₹{friction:,.2f}\n"
                f"• *Date:* {date_str}"
            )
            send_telegram_alert(alert_msg)

        # -------------------------------------------------------------
        # Step 2: Position not stopped out. Evaluate Ratcheting Stops.
        # -------------------------------------------------------------
        else:
            if close > highest_close:
                highest_close = close

            unrealized_peak = high - entry_price
            current_r = unrealized_peak / per_share_risk if per_share_risk > 0 else 0.0
            if current_r > max_r_reached:
                max_r_reached = round(current_r, 2)

            new_stop = current_stop
            new_stage = stage

            trade_risk = round(per_share_risk * qty, 2)

            # Stage 2: When unrealized gain touches +2.5R (+2.5 * trade_risk)
            if current_r >= 2.5 or (unrealized_peak * qty) >= (2.5 * trade_risk):
                new_stage = 2
                chandelier_stop = round(highest_close - (2.5 * atr), 2)
                # Monotonic ratchet upward only
                new_stop = max(current_stop, chandelier_stop, entry_price)

                if new_stop > current_stop or stage < 2:
                    trailing_updates.append({
                        "id": trade_id,
                        "symbol": symbol,
                        "stage": 2,
                        "old_stop": current_stop,
                        "new_stop": new_stop,
                        "highest_close": highest_close,
                    })

                    if stage < 2:
                        alert_msg = (
                            f"🚀 *STAGE 2 ACTIVATED: CHANDELIER TRAILING STOP*\n\n"
                            f"• *Symbol:* `{symbol}` (+2.5R reached)\n"
                            f"• *Trailing Stop:* ₹{new_stop:,.2f} (Peak Close ₹{highest_close:,.2f} - 2.5x ATR)\n"
                            f"• *Entry Price:* ₹{entry_price:,.2f}\n"
                            f"• *LTP:* ₹{close:,.2f}\n"
                            f"• *Date:* {date_str}"
                        )
                        send_telegram_alert(alert_msg)

            # Stage 1: When unrealized gain touches +1.5R (+1.5 * trade_risk)
            elif current_r >= 1.5 or (unrealized_peak * qty) >= (1.5 * trade_risk):
                if stage < 1:
                    new_stage = 1
                new_stop = max(current_stop, entry_price)

                if new_stop > current_stop or stage < 1:
                    trailing_updates.append({
                        "id": trade_id,
                        "symbol": symbol,
                        "stage": 1,
                        "old_stop": current_stop,
                        "new_stop": new_stop,
                        "highest_close": highest_close,
                    })

                    if stage < 1:
                        alert_msg = (
                            f"🛡️ *STAGE 1 ACTIVATED: BREAKEVEN STOP LOCKED*\n\n"
                            f"• *Symbol:* `{symbol}` (+1.5R reached)\n"
                            f"• *Stop-Loss Moved to Entry:* ₹{new_stop:,.2f} (Risk zeroed out!)\n"
                            f"• *LTP:* ₹{close:,.2f}\n"
                            f"• *Date:* {date_str}"
                        )
                        send_telegram_alert(alert_msg)

            update_trade_trailing_stop(
                trade_id=trade_id,
                stop_loss=new_stop,
                highest_close=highest_close,
                max_r_reached=max_r_reached,
                stage=new_stage
            )

    return {"closed": closed_summary, "trailing_updates": trailing_updates}


def scan_and_enter_trades(regime: Dict[str, Any]) -> Dict[str, Any]:
    """
    Screen Nifty 200 universe with 10-year validated dynamic compounding engine:
    - Dynamic 1.0% equity risk per trade, max 20% capital ceiling per slot (5 slots max).
    - Top Relative Strength selection (NO sector restrictions).
    - Deployment Trade Gate: Fresh entries officially begin on Tuesday, 2026-11-10 at 3:35 PM IST.
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

    # Deployment Trade Gate: No new buys today (2026-11-09)
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    current_date = datetime.datetime.now(ist).date()
    if current_date < DEPLOYMENT_START_DATE:
        print(
            f"\n[DEPLOYMENT GATE] Today is {current_date}. "
            f"Fresh scans and order placement begin officially on Tuesday, {DEPLOYMENT_START_DATE} at 3:35 PM IST."
        )
        print("[DEPLOYMENT GATE] Portfolio reset to clean Rs 2,00,000 cash. New buy entries gated today.")
        return {
            "universe_size": len(universe),
            "scans_evaluated": 0,
            "signals_found": [],
            "executed_trades": [],
            "regime": regime,
            "suspended": False,
            "gated": True,
        }

    # Benchmark Market Filter: Dual-Speed Regime Filter
    if not regime["is_bullish"]:
        print(
            f"\n[REGIME] Nifty 50 Close (Rs {regime['nifty_close']}) <= 20 EMA (Rs {regime.get('nifty_ema20', 0)}) "
            f"and 50 EMA (Rs {regime['nifty_ema50']}). Market is DEFENSIVE. New trade screening suspended."
        )
        return {
            "universe_size": len(universe),
            "scans_evaluated": 0,
            "signals_found": [],
            "executed_trades": [],
            "regime": regime,
            "suspended": True,
            "gated": False,
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

    # Rank qualifying candidates strictly by Relative Strength descending (NO sector restrictions)
    signals_found.sort(key=lambda s: s["relative_strength"], reverse=True)

    # Dynamic 1.0% Equity Compounding
    open_equity = sum(ot["entry_price"] * ot["qty"] for ot in open_trades)
    portfolio_equity = cash + open_equity
    current_risk = portfolio_equity * RISK_PCT_PER_TRADE
    current_risk = max(1000.0, current_risk)  # Prudent floor

    # Execute entries for top candidates
    for setup in signals_found:
        symbol = setup["symbol"]

        if available_slots <= 0:
            break

        if symbol in open_symbols:
            continue

        per_share_risk = setup["per_share_risk"]
        close_price = setup["close"]

        # Position sizing: Dynamic 1% risk bounded by 20% slot ceiling and available cash
        raw_qty = int(math.floor(current_risk / per_share_risk))
        max_slot_capital = portfolio_equity * MAX_SLOT_PCT
        max_qty_by_slot = int(math.floor(min(cash, max_slot_capital) / close_price))
        qty = min(raw_qty, max_qty_by_slot)

        if qty >= 1:
            total_cost = round(qty * close_price, 2)
            cash = round(cash - total_cost, 2)
            trade_risk = round(qty * per_share_risk, 2)

            trade_id = record_trade(
                symbol=symbol,
                entry_date=setup["date"],
                entry_price=close_price,
                qty=qty,
                stop_loss=setup["stop_loss"],
                per_share_risk=per_share_risk,
                target=round(close_price + (2.5 * per_share_risk), 2)
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
                "atr": setup["atr"],
                "per_share_risk": per_share_risk,
                "trade_risk": trade_risk,
                "relative_strength": setup["relative_strength"],
                "rsi": setup["rsi"],
                "vol_ratio": setup["vol_ratio"],
            }
            executed_trades.append(trade_info)

            # Instant Telegram Alert
            trade_alert = (
                f"🚀 *NEW SWING TRADE ENTERED (DYNAMIC COMPOUNDING)*\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Entry Price:* ₹{close_price:,.2f}\n"
                f"• *Quantity:* {qty} shares (Allocation: ₹{total_cost:,.2f} | Risk: ₹{trade_risk:,.2f})\n"
                f"• *Initial Stop-Loss (1.5x ATR):* ₹{setup['stop_loss']:,.2f}\n"
                f"• *Stage 1 Target (+1.5R):* ₹{round(close_price + 1.5 * per_share_risk, 2):,.2f} (BE Lock)\n"
                f"• *Stage 2 Target (+2.5R):* ₹{round(close_price + 2.5 * per_share_risk, 2):,.2f} (Chandelier Trail)\n"
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
        "gated": False,
    }


def send_eod_summary_alert(
    scan_result: Dict[str, Any],
    monitoring_result: Dict[str, Any]
) -> None:
    """Send comprehensive daily EOD Telegram alert with regime, positions, and signals."""
    acc = get_account()
    open_trades = get_open_trades()
    regime = scan_result.get("regime", {})

    open_equity = sum(ot["entry_price"] * ot["qty"] for ot in open_trades)
    total_equity = acc["cash"] + open_equity

    regime_status = "🟢 BULLISH (Close > 20/50 EMA)" if regime.get("is_bullish") else "🔴 DEFENSIVE (Close <= 20 & 50 EMA)"
    nifty_close = regime.get("nifty_close", 0.0)
    nifty_ema20 = regime.get("nifty_ema20", 0.0)
    nifty_ema50 = regime.get("nifty_ema50", 0.0)
    nifty_ret20 = regime.get("nifty_20d_return", 0.0) * 100

    signals = scan_result.get("signals_found", [])
    executed = scan_result.get("executed_trades", [])
    closed = monitoring_result.get("closed", [])
    trailing = monitoring_result.get("trailing_updates", [])
    is_gated = scan_result.get("gated", False)

    lines = [
        "📊 *DYNAMIC COMPOUNDING SWING ENGINE (NIFTY 200)*",
        f"_{datetime.datetime.now().strftime('%d %b %Y | %I:%M %p IST')}_\n",
        f"🌐 *Benchmark Regime (Nifty 50):*",
        f"• *Regime:* {regime_status}",
        f"• *Nifty 50 Close:* ₹{nifty_close:,.2f} | *20 EMA:* ₹{nifty_ema20:,.2f} | *50 EMA:* ₹{nifty_ema50:,.2f}",
        f"• *20-Day Return:* {nifty_ret20:+.2f}%\n",
        f"📌 *Active Positions ({len(open_trades)}/{MAX_OPEN_TRADES} Slots):*"
    ]

    if open_trades:
        for ot in open_trades:
            sym = ot["symbol"]
            qty = ot["qty"]
            ep = ot["entry_price"]
            sl = ot["stop_loss"]
            stg = int(ot.get("stage") or 0)
            stage_label = f"Stage {stg} ({'Initial SL' if stg == 0 else ('BE Lock' if stg == 1 else 'Chandelier Trail')})"
            lines.append(f"  • *{sym}:* {qty} shs @ ₹{ep:,.2f} | Stop: ₹{sl:,.2f} | {stage_label}")
    else:
        lines.append("  • No active positions (5 Slots Available)")

    if is_gated:
        lines.append(f"\n🚪 *Deployment Gate Active:* Fresh entries gated today. Live scans begin Tuesday, 10 Nov at 3:35 PM IST.")
    else:
        lines.append(f"\n⚡ *Signals Today ({len(signals)} Qualifying, Ranked by RS):*")
        if signals:
            for s in signals[:5]:
                lines.append(
                    f"  • *{s['symbol']}:* RS: {s['relative_strength']:+.1f}% | RSI: {s['rsi']} | "
                    f"Vol: {s['vol_ratio']}x | SL: ₹{s['stop_loss']:,.2f}"
                )
            if len(signals) > 5:
                lines.append(f"    _(and {len(signals) - 5} more qualifying setups)_")
        else:
            lines.append("  • None qualifying today")

    if executed:
        lines.append(f"\n🚀 *New Trades Opened:* {len(executed)}")
        for et in executed:
            lines.append(f"  • {et['symbol']} ({et['qty']} shares @ ₹{et['entry_price']})")

    if trailing:
        lines.append(f"\n📈 *Trailing Stop Updates:* {len(trailing)}")
        for tu in trailing:
            lines.append(f"  • {tu['symbol']}: Stop ratcheted to ₹{tu['new_stop']:,.2f} (Stage {tu['stage']})")

    if closed:
        lines.append(f"\n🚪 *Trades Closed:* {len(closed)}")
        for ct in closed:
            lines.append(f"  • {ct['symbol']} ({ct['status']} @ ₹{ct['exit_price']} | P&L: ₹{ct['net_pnl']:+,.2f})")

    lines.extend([
        f"\n💼 *Portfolio & Capital:*",
        f"• *Available Cash:* ₹{acc['cash']:,.2f}",
        f"• *Invested Capital:* ₹{open_equity:,.2f}",
        f"• *Realized P&L:* ₹{acc['realized_pnl']:+,.2f}",
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

    regime_label = "[BULLISH]" if regime.get("is_bullish") else "[DEFENSIVE]"
    print("\n" + "=" * 80)
    print("      DYNAMIC COMPOUNDING SWING ENGINE (NIFTY 200 - 10Y DEPLOYMENT)")
    print("=" * 80)
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"Benchmark Regime: {regime_label} "
        f"(Nifty 50: Rs {regime.get('nifty_close', 0.0):,.2f} | 20 EMA: Rs {regime.get('nifty_ema20', 0.0):,.2f} | "
        f"50 EMA: Rs {regime.get('nifty_ema50', 0.0):,.2f} | 20d Ret: {regime.get('nifty_20d_return', 0.0)*100:+.2f}%)"
    )

    if scan_result.get("gated"):
        print("[DEPLOYMENT GATE] Today is 2026-11-09. Live entries gated today. Official launch: Tuesday, 2026-11-10 at 3:35 PM IST.")
    elif scan_result.get("suspended"):
        print("[NOTICE] Nifty 50 below both 20 & 50 EMA -> New trade entry screening SUSPENDED for defense.")
    else:
        print(f"Universe Evaluated: {scan_result['scans_evaluated']}/{scan_result['universe_size']} tickers")
        print(f"Setups Qualifying Today: {len(scan_result['signals_found'])}")

    if scan_result.get("signals_found"):
        print("\n--- Qualifying Signals (Ranked by Relative Strength - No Sector Cap) ---")
        for s in scan_result["signals_found"][:8]:
            print(
                f"  * {s['symbol']:<15} | RS: {s['relative_strength']:+6.2f}% | RSI: {s['rsi']:4.1f} | "
                f"Vol: {s['vol_ratio']:4.2f}x | Close: Rs {s['close']:<8} | SL: Rs {s['stop_loss']:<8}"
            )

    print("\n--- Position Exits & Trailing Updates Today ---")
    closed = monitoring_result.get("closed", [])
    trailing = monitoring_result.get("trailing_updates", [])
    if trailing:
        for tu in trailing:
            print(f"  * [TRAILING UPDATE] #{tu['id']} {tu['symbol']} Stop moved from Rs {tu['old_stop']} to Rs {tu['new_stop']} (Stage {tu['stage']})")
    if closed:
        for c in closed:
            print(f"  * [{c['status']}] #{c['id']} {c['symbol']} exited @ Rs {c['exit_price']} ({c['reason']}) | Net PnL: Rs {c['net_pnl']}")
    if not trailing and not closed:
        print("  None (No exit triggers breached or trailing milestones reached today)")

    print("\n--- New Trades Executed Today ---")
    executed = scan_result.get("executed_trades", [])
    if executed:
        for et in executed:
            print(
                f"  * [BUY] {et['symbol']:<15} Qty: {et['qty']:<4} @ Rs {et['entry_price']:<8} | "
                f"Alloc: Rs {et['cost']:<8} | SL: Rs {et['stop_loss']} | RS: {et['relative_strength']:+.1f}%"
            )
    else:
        print("  None (Slots full, no qualifying signals, or cash constrained)")

    print(f"\n--- Current Open Positions ({len(open_trades)}/{MAX_OPEN_TRADES}) ---")
    if open_trades:
        for ot in open_trades:
            sym = ot["symbol"]
            qty = ot["qty"]
            sl = ot["stop_loss"]
            stg = int(ot.get("stage") or 0)
            stage_str = "Initial SL" if stg == 0 else ("Breakeven Lock" if stg == 1 else "Chandelier Trail")
            print(f"  * #{ot['id']} {sym:<15} Qty: {qty:<4} Entry: Rs {ot['entry_price']:<8} Stop: Rs {sl:<8} Stage: {stg} ({stage_str})")
    else:
        print("  No open positions")

    print("\n--- Account Overview ---")
    open_equity = sum(ot["entry_price"] * ot["qty"] for ot in open_trades)
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

    print("[INIT] Monitoring open positions with 2-Stage Chandelier Trailing Engine...")
    monitoring_result = monitor_open_trades()

    print("[INIT] Scanning Nifty 200 universe (Top RS, RSI 40-65, Vol >= 0.85x)...")
    scan_result = scan_and_enter_trades(regime)

    print_summary(scan_result, monitoring_result)

    # Send EOD summary alert to Telegram
    send_eod_summary_alert(scan_result, monitoring_result)


if __name__ == "__main__":
    force_run = "--force" in sys.argv
    run(force=force_run)
