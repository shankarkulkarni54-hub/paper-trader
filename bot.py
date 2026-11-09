import datetime
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
    get_account,
    update_account,
    get_open_trades,
    record_trade,
    close_trade,
    get_all_trades,
)

# Configuration
INITIAL_CAPITAL = 200000.0
RISK_PER_TRADE = 2000.0   # 1% of starting capital
MAX_OPEN_TRADES = 4
FRICTION_RATE = 0.0015     # 0.15% round-trip friction / brokerage

NIFTY_200_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
LOCAL_CSV_NAME = "nifty200.csv"


# Helper to auto-load .env file if present
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

# Official NSE Trading Holidays (including multi-year calendar and fixed annual holidays)
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
    Check if today's date (in Asia/Kolkata timezone) falls on a weekend (Saturday or Sunday)
    or matches official NSE trading holidays.
    Returns True if it is a holiday/weekend, else False.
    """
    if dt is None:
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        dt = datetime.datetime.now(ist).date()

    # Weekend check: 5 = Saturday, 6 = Sunday
    if dt.weekday() in (5, 6):
        return True

    date_str = dt.strftime("%Y-%m-%d")
    month_day = dt.strftime("%m-%d")

    # Match against known NSE trading holiday calendar
    if date_str in NSE_HOLIDAYS:
        return True

    # Annual fixed national holidays (Republic Day, Maharashtra Day, Independence Day, Gandhi Jayanti, Christmas)
    if month_day in {"01-26", "05-01", "08-15", "10-02", "12-25"}:
        return True

    return False


def send_telegram_alert(message: str) -> bool:
    """
    Send an alert message via Telegram Bot API using Markdown formatting.
    Falls back gracefully if token is not yet configured.
    """
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


def load_nifty200_universe() -> List[str]:
    """
    Fetch the official Nifty 200 stock universe from NSE archives.
    Caches to local CSV and falls back to cached copy if network is unavailable.
    Appends .NS suffix to symbols for Yahoo Finance.
    """
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOCAL_CSV_NAME)

    # Attempt to fetch latest list from NSE
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

    # Fallback safety list if file is somehow missing
    if not tickers:
        print("[WARN] Using fallback liquid tickers")
        tickers = [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS",
            "INFY.NS", "ITC.NS", "HINDUNILVR.NS", "LT.NS", "SBIN.NS"
        ]

    return tickers


def fetch_all_ticker_data(symbols: List[str], period: str = "1y") -> Dict[str, pd.DataFrame]:
    """
    Fetch daily historical data for all symbols in a single batch call
    using yf.download(tickers, period='1y', group_by='ticker', threads=True).
    """
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
    """Calculate 20 EMA, 50 EMA, 200 EMA, 14 RSI, and 14 ATR."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    df["EMA_20"] = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
    df["EMA_50"] = ta.trend.EMAIndicator(close=close, window=50).ema_indicator()
    df["EMA_200"] = ta.trend.EMAIndicator(close=close, window=200).ema_indicator()
    df["RSI_14"] = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    df["ATR_14"] = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

    return df


def check_entry_setup(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Check if the latest daily bar satisfies entry conditions:
    1. Trend: Close > 50 EMA > 200 EMA
    2. Momentum: 45 <= RSI <= 62
    3. Mean reversion / pullback: Daily Close within 2.5% of 20 EMA

    Stop Loss: 5-day low or 1.5x ATR
    Target: 1:2 Risk to Reward (Close + 2 * (Close - SL))
    """
    if len(df) < 200:
        return None

    latest = df.iloc[-1]
    close = float(latest["Close"])
    ema_20 = float(latest["EMA_20"])
    ema_50 = float(latest["EMA_50"])
    ema_200 = float(latest["EMA_200"])
    rsi = float(latest["RSI_14"])
    atr = float(latest["ATR_14"])

    # Check for NaN values
    if any(pd.isna([close, ema_20, ema_50, ema_200, rsi, atr])):
        return None

    # Condition 1: Trend filter
    trend_ok = (close > ema_50) and (ema_50 > ema_200)

    # Condition 2: RSI filter (between 45 and 62)
    rsi_ok = (45.0 <= rsi <= 62.0)

    # Condition 3: Close within 2.5% of 20 EMA
    pct_from_ema20 = abs(close - ema_20) / ema_20
    ema20_ok = (pct_from_ema20 <= 0.025)

    if not (trend_ok and rsi_ok and ema20_ok):
        return None

    # Stop Loss calculation: 5-day low or 1.5x ATR
    lowest_5d = float(df["Low"].iloc[-5:].min())
    sl_atr = close - (1.5 * atr)

    if lowest_5d < close:
        stop_loss = round(min(lowest_5d, sl_atr), 2)
    else:
        stop_loss = round(sl_atr, 2)

    risk_per_share = close - stop_loss
    if risk_per_share <= 0.05:
        return None

    target = round(close + (2.0 * risk_per_share), 2)
    date_str = str(latest["Date"])[:10]

    return {
        "date": date_str,
        "close": round(close, 2),
        "ema_20": round(ema_20, 2),
        "ema_50": round(ema_50, 2),
        "ema_200": round(ema_200, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "stop_loss": stop_loss,
        "target": target,
        "risk_per_share": round(risk_per_share, 2),
    }


def monitor_open_trades() -> List[Dict[str, Any]]:
    """
    Monitor existing open trades:
    - Check if latest High/Low hit Target or Stop Loss.
    - If hit, close trade, deduct 0.15% friction/brokerage, and credit/debit cash.
    - Send an instant Telegram notification alert.
    """
    open_trades = get_open_trades()
    closed_summary = []

    if not open_trades:
        return closed_summary

    acc = get_account()
    cash = acc["cash"]
    realized_pnl = acc["realized_pnl"]

    symbols = list({t["symbol"] for t in open_trades})
    stock_data = fetch_all_ticker_data(symbols, period="5d")

    for trade in open_trades:
        symbol = trade["symbol"]
        df = stock_data.get(symbol)
        if df is None or df.empty:
            continue

        latest = df.iloc[-1]
        high = float(latest["High"])
        low = float(latest["Low"])
        close = float(latest["Close"])
        date_str = str(latest["Date"])[:10]

        target = float(trade["target"])
        stop_loss = float(trade["stop_loss"])
        entry_price = float(trade["entry_price"])
        qty = int(trade["qty"])

        status = None
        exit_price = None

        # Check target or SL hit
        if low <= stop_loss and high >= target:
            status = "CLOSED_SL"
            exit_price = stop_loss
        elif high >= target:
            status = "CLOSED_TARGET"
            exit_price = target
        elif low <= stop_loss:
            status = "CLOSED_SL"
            exit_price = stop_loss

        if status and exit_price:
            gross_pnl = (exit_price - entry_price) * qty
            turnover = (entry_price + exit_price) * qty
            friction = round(FRICTION_RATE * turnover, 2)
            net_pnl = round(gross_pnl - friction, 2)

            # Cash return: (exit_price * qty) - friction
            cash_returned = round((exit_price * qty) - friction, 2)
            cash = round(cash + cash_returned, 2)
            realized_pnl = round(realized_pnl + net_pnl, 2)

            update_account(cash=cash, realized_pnl=realized_pnl)
            close_trade(
                trade_id=trade["id"],
                exit_date=date_str,
                exit_price=round(exit_price, 2),
                pnl=net_pnl,
                status=status,
            )

            closed_summary.append({
                "id": trade["id"],
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "qty": qty,
                "net_pnl": net_pnl,
                "status": status,
                "exit_date": date_str,
            })

            # Instant Telegram Alert for Target or Stop Loss Hit
            status_title = "🎯 *TARGET HIT - TRADE CLOSED*" if status == "CLOSED_TARGET" else "🛑 *STOP-LOSS HIT - TRADE CLOSED*"
            alert_msg = (
                f"{status_title}\n\n"
                f"• *Symbol:* `{symbol}`\n"
                f"• *Status:* `{status}`\n"
                f"• *Entry Price:* ₹{entry_price:,.2f}\n"
                f"• *Exit Price:* ₹{exit_price:,.2f}\n"
                f"• *Quantity:* {qty}\n"
                f"• *Net P&L:* ₹{net_pnl:+,.2f}\n"
                f"• *Friction:* ₹{friction:,.2f}\n"
                f"• *Date:* {date_str}"
            )
            send_telegram_alert(alert_msg)

    return closed_summary


def scan_and_enter_trades() -> Dict[str, Any]:
    """
    Scan Nifty 200 stocks for entry setup.
    If conditions match and slots available, calculate position size and enter trade.
    Sends instant Telegram alert for every newly opened trade.
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
    print(f"\n[SCAN] Batch downloading 1-year data for {len(universe)} Nifty 200 tickers...")
    print(f"[SCAN] Open slots available: {available_slots}/{MAX_OPEN_TRADES}")

    data_map = fetch_all_ticker_data(universe, period="1y")

    for symbol in universe:
        df = data_map.get(symbol)
        if df is None or len(df) < 200:
            continue

        scans_evaluated += 1
        df = calculate_indicators(df)
        setup = check_entry_setup(df)

        if setup:
            setup["symbol"] = symbol
            signals_found.append(setup)

            # Skip if already in open positions
            if symbol in open_symbols:
                continue

            if available_slots > 0:
                risk_per_share = setup["risk_per_share"]
                raw_qty = int(RISK_PER_TRADE / risk_per_share)

                # Check cash constraint
                max_qty_by_cash = int(cash // setup["close"])
                qty = min(raw_qty, max_qty_by_cash)

                if qty >= 1:
                    total_cost = round(qty * setup["close"], 2)
                    cash = round(cash - total_cost, 2)

                    trade_id = record_trade(
                        symbol=symbol,
                        entry_date=setup["date"],
                        entry_price=setup["close"],
                        qty=qty,
                        stop_loss=setup["stop_loss"],
                        target=setup["target"],
                    )

                    update_account(cash=cash, realized_pnl=realized_pnl)
                    open_symbols.add(symbol)
                    available_slots -= 1

                    trade_info = {
                        "id": trade_id,
                        "symbol": symbol,
                        "entry_date": setup["date"],
                        "entry_price": setup["close"],
                        "qty": qty,
                        "cost": total_cost,
                        "stop_loss": setup["stop_loss"],
                        "target": setup["target"],
                        "risk_per_share": risk_per_share,
                    }
                    executed_trades.append(trade_info)

                    # Instant Telegram Notification for New Trade Entered
                    trade_alert = (
                        f"🚀 *NEW SWING TRADE ENTERED*\n\n"
                        f"• *Symbol:* `{symbol}`\n"
                        f"• *Entry Price:* ₹{setup['close']:,.2f}\n"
                        f"• *Quantity:* {qty} (Cost: ₹{total_cost:,.2f})\n"
                        f"• *Stop Loss:* ₹{setup['stop_loss']:,.2f}\n"
                        f"• *Target (1:2):* ₹{setup['target']:,.2f}\n"
                        f"• *Risk (1%):* ₹{round(risk_per_share * qty, 2):,.2f}\n"
                        f"• *20 EMA:* ₹{setup['ema_20']:,.2f} | *RSI:* {setup['rsi']}\n"
                        f"• *Date:* {setup['date']}"
                    )
                    send_telegram_alert(trade_alert)

    return {
        "universe_size": len(universe),
        "scans_evaluated": scans_evaluated,
        "signals_found": signals_found,
        "executed_trades": executed_trades,
    }


def send_eod_summary_alert(scan_result: Dict[str, Any], closed_trades: List[Dict[str, Any]]) -> None:
    """Send EOD summary Telegram alert after the daily scan finishes."""
    acc = get_account()
    open_trades = get_open_trades()
    open_equity = sum(ot["entry_price"] * ot["qty"] for ot in open_trades)
    total_equity = acc["cash"] + open_equity

    signals_count = len(scan_result.get("signals_found", []))
    executed_count = len(scan_result.get("executed_trades", []))
    closed_count = len(closed_trades)

    msg = (
        f"📊 *EOD SCAN SUMMARY (NIFTY 200)*\n"
        f"_{datetime.datetime.now().strftime('%d %b %Y | %I:%M %p IST')}_\n\n"
        f"• *Universe Evaluated:* {scan_result.get('scans_evaluated', 0)} / {scan_result.get('universe_size', 200)} tickers\n"
        f"• *Signals Found:* {signals_count}\n"
        f"• *Trades Closed Today:* {closed_count}\n"
        f"• *New Trades Opened:* {executed_count}\n"
        f"• *Active Positions:* {len(open_trades)} / {MAX_OPEN_TRADES}\n\n"
        f"💼 *Portfolio Overview:*\n"
        f"• *Available Cash:* ₹{acc['cash']:,.2f}\n"
        f"• *Realized P&L:* ₹{acc['realized_pnl']:+,.2f}\n"
        f"• *Open Positions Value:* ₹{open_equity:,.2f}\n"
        f"• *Estimated Equity:* ₹{total_equity:,.2f}"
    )
    send_telegram_alert(msg)


def print_summary(scan_result: Dict[str, Any], closed_trades: List[Dict[str, Any]]) -> None:
    """Print a clean, structured console summary of the trading session."""
    acc = get_account()
    open_trades = get_open_trades()

    print("\n" + "=" * 78)
    print("           AUTOMATED SWING PAPER TRADING BOT SUMMARY (NIFTY 200)")
    print("=" * 78)
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Universe Size: {scan_result['universe_size']} | Evaluated (>=200 bars): {scan_result['scans_evaluated']}")
    print(f"Signals Identified: {len(scan_result['signals_found'])}")

    if scan_result["signals_found"]:
        print("\n--- Signals Qualifying Today ---")
        for s in scan_result["signals_found"]:
            print(
                f"  * {s['symbol']:<16} | Close: Rs {s['close']:<8} | 20 EMA: Rs {s['ema_20']:<8} | "
                f"RSI: {s['rsi']:<5} | SL: Rs {s['stop_loss']:<8} | Target: Rs {s['target']:<8}"
            )

    print("\n--- Trades Monitored & Closed ---")
    if closed_trades:
        for ct in closed_trades:
            print(f"  * Trade #{ct['id']} {ct['symbol']} closed via {ct['status']} at Rs {ct['exit_price']} (PnL: Rs {ct['net_pnl']})")
    else:
        print("  None (No exit triggers hit for active positions)")

    print("\n--- Trades Executed Today ---")
    if scan_result["executed_trades"]:
        for et in scan_result["executed_trades"]:
            print(
                f"  * [BUY] {et['symbol']:<16} Qty: {et['qty']:<4} @ Rs {et['entry_price']:<8} | "
                f"Cost: Rs {et['cost']} | SL: Rs {et['stop_loss']} | Target: Rs {et['target']}"
            )
    else:
        print("  None (Either no new signals, slots full, or insufficient cash)")

    print(f"\n--- Current Open Positions ({len(open_trades)}/{MAX_OPEN_TRADES}) ---")
    if open_trades:
        for ot in open_trades:
            print(
                f"  * #{ot['id']} {ot['symbol']:<16} Qty: {ot['qty']:<4} Entry: Rs {ot['entry_price']:<8} "
                f"SL: Rs {ot['stop_loss']:<8} Target: Rs {ot['target']:<8} Date: {ot['entry_date']}"
            )
    else:
        print("  No open positions")

    print("\n--- Account Overview ---")
    print(f"  Initial Capital : Rs {acc['initial_capital']:,.2f}")
    print(f"  Available Cash  : Rs {acc['cash']:,.2f}")
    print(f"  Realized PnL    : Rs {acc['realized_pnl']:,.2f}")

    open_equity = sum(ot["entry_price"] * ot["qty"] for ot in open_trades)
    total_account_value = acc["cash"] + open_equity
    print(f"  Open Positions  : Rs {open_equity:,.2f} (at entry cost)")
    print(f"  Estimated Equity: Rs {total_account_value:,.2f}")
    print("=" * 78 + "\n")


def run(force: bool = False):
    """Main execution cycle."""
    if not force and is_nse_holiday():
        print("[INFO] Today is an NSE market holiday or weekend. Skipping scan and silencing alerts.")
        sys.exit(0)

    init_db()

    print("[INIT] Monitoring open trades for exit conditions...")
    closed_trades = monitor_open_trades()

    print("[INIT] Scanning Nifty 200 stocks for potential setups...")
    scan_result = scan_and_enter_trades()

    print_summary(scan_result, closed_trades)

    # Send EOD summary alert to Telegram
    send_eod_summary_alert(scan_result, closed_trades)


if __name__ == "__main__":
    run()
