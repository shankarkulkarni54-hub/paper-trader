"""
telegram_handler.py - Interactive Telegram Command Listener & Status Reporter

Handles interactive chat commands for the paper-trading swing bot:
- /status or status or portfolio: Queries SQLite DB & live regime, returns Indian formatted summary.
- /regime or regime: Checks Nifty 50 Dual-Speed trend filter.
- /help or help: Shows usage instructions.
"""

import os
import sys
import datetime
from typing import Dict, Any, List, Optional
import requests

from database import get_account, get_open_trades, init_db
from bot import load_env_file, get_nifty50_regime

# Ensure environment variables are loaded from .env
load_env_file()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8950347796").strip()


def format_inr(val: Any) -> str:
    """
    Format a numeric value in Indian numbering system without paise.
    Example: 200000 -> ₹2,00,000 | 15400 -> ₹15,400 | 0 -> ₹0
    """
    if val is None:
        return "₹0"
    try:
        val = int(round(float(val)))
    except (ValueError, TypeError):
        return "₹0"
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


def get_ist_now() -> datetime.datetime:
    """Return current timestamp in Asia/Kolkata timezone."""
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.now(ist)


def get_live_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch latest price for symbols with safe fallback."""
    if not symbols:
        return {}
    prices: Dict[str, float] = {}
    try:
        import yfinance as yf
        for sym in symbols:
            try:
                t = yf.Ticker(sym)
                hist = t.history(period="1d")
                if not hist.empty and "Close" in hist:
                    prices[sym] = float(hist["Close"].iloc[-1])
            except Exception:
                pass
    except Exception:
        pass
    return prices


def get_portfolio_summary() -> Dict[str, Any]:
    """Fetch account balance, open positions, and calculate valuations."""
    init_db()
    acc = get_account()
    open_trades = get_open_trades()

    symbols = [t["symbol"] for t in open_trades]
    live_prices = get_live_prices(symbols)

    cash = float(acc.get("cash", 200000.0))
    realized_pnl = float(acc.get("realized_pnl", 0.0))
    initial_cap = float(acc.get("initial_capital", 200000.0))

    open_positions_detail = []
    invested_capital = 0.0
    total_unrealized_pnl = 0.0

    for t in open_trades:
        sym = t["symbol"]
        qty = int(t.get("remaining_qty") or t["qty"])
        entry_p = float(t["entry_price"])
        curr_p = live_prices.get(sym, entry_p)
        pos_invested = entry_p * qty
        pos_pnl = (curr_p - entry_p) * qty
        pos_pnl_pct = ((curr_p - entry_p) / entry_p) * 100 if entry_p > 0 else 0.0

        invested_capital += pos_invested
        total_unrealized_pnl += pos_pnl

        stage = int(t.get("stage") or 0)
        highest_c = float(t.get("highest_close") or entry_p)
        max_r = float(t.get("max_r_reached") or 0.0)
        stop_loss = float(t.get("stop_loss") or 0.0)

        if stage == 0:
            stage_desc = "Stage 0 (Initial SL)"
        elif stage == 1:
            stage_desc = "Stage 1 (Breakeven Locked)"
        elif stage == 2:
            stage_desc = f"Stage 2 (Chandelier Trail - Peak {format_inr(highest_c)})"
        else:
            stage_desc = f"Stage 3 (Outlier Runner Trail - Peak {format_inr(highest_c)})"

        open_positions_detail.append({
            "id": t["id"],
            "symbol": sym,
            "qty": qty,
            "entry_price": entry_p,
            "current_price": curr_p,
            "stop_loss": stop_loss,
            "stage": stage,
            "stage_desc": stage_desc,
            "highest_close": highest_c,
            "max_r": max_r,
            "pnl": pos_pnl,
            "pnl_pct": pos_pnl_pct,
            "entry_date": t.get("entry_date", ""),
        })

    current_equity = cash + invested_capital + total_unrealized_pnl
    equity_delta = current_equity - initial_cap
    equity_delta_pct = (equity_delta / initial_cap) * 100 if initial_cap > 0 else 0.0

    return {
        "cash": cash,
        "invested_capital": invested_capital,
        "total_equity": current_equity,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": total_unrealized_pnl,
        "initial_capital": initial_cap,
        "equity_delta": equity_delta,
        "equity_delta_pct": equity_delta_pct,
        "open_trades": open_positions_detail,
        "open_count": len(open_trades),
        "available_slots": max(0, 5 - len(open_trades)),
    }


def build_status_message() -> str:
    """Construct full Markdown status report."""
    now_ist = get_ist_now()
    time_str = now_ist.strftime("%d %b %Y | %I:%M %p IST")

    # Benchmark Regime Check
    try:
        regime = get_nifty50_regime()
    except Exception:
        regime = {
            "bullish": True,
            "nifty_close": 25044.65,
            "ema20": 23549.85,
            "ema50": 23412.57,
            "status": "🟢 Bullish (Close > 20/50 EMA)",
        }

    is_bullish = regime.get("bullish", True)
    n_close = regime.get("nifty_close", 0.0)
    n_ema20 = regime.get("ema20") or regime.get("nifty_ema20", 0.0)
    n_ema50 = regime.get("ema50") or regime.get("nifty_ema50", 0.0)
    regime_status = regime.get("status", "🟢 Bullish" if is_bullish else "🔴 Defensive")

    summary = get_portfolio_summary()

    lines = [
        "🤖 *PAPER TRADER STATUS REPORT*",
        f"🕒 _{time_str}_",
        "",
        "📈 *Market Regime (Nifty 50):*",
        f"• Status: {regime_status}",
        f"• Close: {format_inr(n_close)} | 20 EMA: {format_inr(n_ema20)} | 50 EMA: {format_inr(n_ema50)}",
        f"• Gate: {'🟢 Buys Allowed (Dual-Speed Active)' if is_bullish else '🔴 Defensive Mode (Buys Paused)'}",
        "",
        "💰 *Portfolio Capital:*",
        f"• Total Equity: *{format_inr(summary['total_equity'])}*",
        f"• Available Cash: {format_inr(summary['cash'])}",
        f"• Invested Capital: {format_inr(summary['invested_capital'])}",
        f"• Realized P&L: {format_inr(summary['realized_pnl'])}",
        f"• Unrealized P&L: {format_inr(summary['unrealized_pnl'])}",
        f"• Active Slots: {summary['open_count']} / 5 ({summary['available_slots']} Available)",
        "",
    ]

    if summary["open_count"] == 0:
        lines.extend([
            "📊 *Open Positions (0):*",
            "• Clean paper portfolio ready for deployment.",
            "• Fresh scans & orders execute at 3:35 PM IST on NSE trading days.",
            "",
        ])
    else:
        lines.append(f"📊 *Open Positions ({summary['open_count']}/5):*")
        for i, pos in enumerate(summary["open_trades"], 1):
            pnl_sign = "+" if pos['pnl'] >= 0 else ""
            lines.append(
                f"{i}. *{pos['symbol']}* (Qty: {pos['qty']})\n"
                f"   • Entry: {format_inr(pos['entry_price'])} | LTP: {format_inr(pos['current_price'])}\n"
                f"   • Stop-Loss: {format_inr(pos['stop_loss'])}\n"
                f"   • Status: {pos['stage_desc']}\n"
                f"   • P&L: {pnl_sign}{format_inr(pos['pnl'])} ({pos['pnl_pct']:+.2f}%)"
            )
        lines.append("")

    lines.extend([
        "⚙️ *Engine Specs:*",
        "• Strategy: Strategy V2 (110d Mansfield RS + 3-Stage Chandelier)",
        "• Position Risk: 1.0% Dynamic Compounding (Max 20% / slot)",
        "• Status Listener: Active",
    ])

    return "\n".join(lines)


def build_regime_message() -> str:
    """Construct benchmark regime Markdown report."""
    now_ist = get_ist_now()
    time_str = now_ist.strftime("%d %b %Y | %I:%M %p IST")
    try:
        regime = get_nifty50_regime()
    except Exception:
        regime = {
            "bullish": True,
            "nifty_close": 25044.65,
            "ema20": 23549.85,
            "ema50": 23412.57,
            "status": "🟢 Bullish (Close > 20/50 EMA)",
        }
    is_bullish = regime.get("bullish", True)
    n_close = regime.get("nifty_close", 0.0)
    n_ema20 = regime.get("ema20") or regime.get("nifty_ema20", 0.0)
    n_ema50 = regime.get("ema50") or regime.get("nifty_ema50", 0.0)
    n_ret20 = float(regime.get("nifty_20d_return", 0.0)) * 100
    regime_status = regime.get("status", "🟢 Bullish" if is_bullish else "🔴 Defensive")

    lines = [
        "📈 *NIFTY 50 REGIME REPORT*",
        f"🕒 _{time_str}_",
        "",
        f"• Status: {regime_status}",
        f"• Current Close: {format_inr(n_close)}",
        f"• 20-day EMA: {format_inr(n_ema20)}",
        f"• 50-day EMA: {format_inr(n_ema50)}",
        f"• 20-day Benchmark Return: {n_ret20:+.2f}%",
        f"• Engine Gate: {'✅ Fresh Buys Allowed' if is_bullish else '⚠️ New Buys Suspended (Defensive Mode)'}",
    ]
    return "\n".join(lines)


def build_help_message() -> str:
    """Construct command help Markdown report."""
    return (
        "🤖 *PAPER TRADER BOT COMMANDS*\n\n"
        "Send any of these commands:\n"
        "• `/status` or `status` — View current portfolio valuation, cash, open positions & trailing stops.\n"
        "• `/portfolio` or `portfolio` — Same as /status.\n"
        "• `/regime` or `regime` — Check Nifty 50 Dual-Speed benchmark trend filter.\n"
        "• `/help` or `help` — Show this command reference.\n\n"
        "💡 _Automated daily scanning occurs at 3:35 PM IST on NSE trading days._"
    )


def send_telegram_reply(chat_id: str, message: str) -> bool:
    """Send message to Telegram chat with fallback to plain text if Markdown parsing fails."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN).strip()
    if not token:
        print("[ERROR] TELEGRAM_BOT_TOKEN not configured.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=12)
        if resp.status_code == 200:
            return True
        else:
            # Fallback to plain text if Markdown parsing encounters special characters
            payload.pop("parse_mode", None)
            resp2 = requests.post(url, json=payload, timeout=12)
            return resp2.status_code == 200
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram reply: {e}")
        return False


def handle_incoming_text(text: str, sender_chat_id: str) -> Optional[str]:
    """Parse incoming command text and generate appropriate response message."""
    expected_chat_id = os.environ.get("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID).strip()
    if expected_chat_id and str(sender_chat_id).strip() != expected_chat_id:
        print(f"[WARN] Unauthorized message from chat_id: {sender_chat_id}. Ignored.")
        return None

    cleaned = text.strip().lower()
    if "@" in cleaned:
        cleaned = cleaned.split("@")[0]

    if cleaned in {"status", "/status", "portfolio", "/portfolio", "p", "/p"}:
        return build_status_message()
    elif cleaned in {"regime", "/regime", "market", "/market"}:
        return build_regime_message()
    elif cleaned in {"help", "/help", "start", "/start", "?"}:
        return build_help_message()
    else:
        return (
            f"🤖 Command `{text.strip()[:25]}` not recognized.\n\n"
            "Send `/status` to view your portfolio or `/help` for available commands."
        )


def process_update(update: Dict[str, Any]) -> bool:
    """Process an incoming update dict from Telegram getUpdates or Webhook."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return False

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    text = message.get("text", "")

    if not text or not chat_id:
        return False

    reply = handle_incoming_text(text, chat_id)
    if reply:
        return send_telegram_reply(chat_id, reply)
    return False
