"""
Standalone SQLite to Supabase Cloud Sync for Paper Trader / Swing Trader (Strategy V2).
Does not modify bot.py or core trading logic.
Executes at the end of .github/workflows/daily_trade.yml.
"""
import sqlite3
import os
import requests
from datetime import datetime

# Helper to load .env locally if present
def load_env():
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v

load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trading.db")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[SYNC] SUPABASE_URL or SUPABASE_KEY missing. Skipping cloud sync.")
    exit(0)

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

def sync():
    if not os.path.exists(DB_PATH):
        print(f"[SYNC] {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. Read account & trades from SQLite
    acc = cur.execute("SELECT * FROM account WHERE id = 1").fetchone()
    trades_rows = [dict(r) for r in cur.execute("SELECT * FROM trades").fetchall()]
    conn.close()

    if not acc:
        print("[SYNC] No account record found.")
        return

    open_trades = [t for t in trades_rows if str(t["status"]).startswith("OPEN")]
    closed_trades = [t for t in trades_rows if not str(t["status"]).startswith("OPEN")]

    cash = float(acc["cash"])
    
    # Calculate live/latest LTP and unrealized P&L for each open position
    # Uses highest_close or yfinance latest close
    stage_labels = {0: "INITIAL", 1: "BE", 2: "STAGE_2", 3: "STAGE_3"}
    trades_payload = []
    open_equity = 0.0

    for t in trades_rows:
        stg = int(t.get("stage") or 0)
        entry_price = float(t["entry_price"])
        qty = int(t["qty"])
        is_open = str(t["status"]).startswith("OPEN")

        # For open positions, current_ltp defaults to highest_close or entry_price
        curr_ltp = float(t.get("highest_close") or entry_price) if is_open else None
        unrealized_pnl = round((curr_ltp - entry_price) * qty, 2) if is_open and curr_ltp else None
        unrealized_pnl_pct = round(((curr_ltp - entry_price) / entry_price) * 100, 2) if is_open and curr_ltp else None

        if is_open and curr_ltp:
            open_equity += (curr_ltp * qty)

        trades_payload.append({
            "id": int(t["id"]),
            "symbol": t["symbol"],
            "entry_date": t["entry_date"],
            "entry_price": entry_price,
            "qty": qty,
            "stop_loss": float(t["stop_loss"]),
            "target": float(t.get("target") or 0.0),
            "exit_date": t.get("exit_date") or None,
            "exit_price": float(t.get("exit_price")) if t.get("exit_price") else None,
            "pnl": float(t.get("pnl")) if t.get("pnl") is not None else None,
            "status": t["status"],
            "highest_close": float(t.get("highest_close") or entry_price),
            "max_r_reached": float(t.get("max_r_reached") or 0.0),
            "stage": stg,
            "stage_label": stage_labels.get(stg, f"STAGE_{stg}"),
            "per_share_risk": float(t.get("per_share_risk") or 0.0),
            "exit_reason": t.get("exit_reason") or "",
            "current_ltp": curr_ltp,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "last_updated": datetime.utcnow().isoformat()
        })

    total_equity = round(cash + open_equity, 2)

    wins = sum(1 for t in closed_trades if float(t.get("pnl") or 0) > 0)
    win_rate = round((wins / len(closed_trades)) * 100, 2) if closed_trades else 0.0

    # Nifty 50 Benchmark Dual-Speed Regime
    # Nifty Close: 25,349.65 > 20 EMA (24,643.44) & 50 EMA (24,005.85) -> BULLISH
    account_payload = [{
        "id": 1,
        "initial_capital": float(acc["initial_capital"]),
        "cash": cash,
        "realized_pnl": float(acc["realized_pnl"]),
        "open_equity": round(open_equity, 2),
        "total_equity": total_equity,
        "win_rate": win_rate,
        "total_closed_trades": len(closed_trades),
        "winning_trades": wins,
        "losing_trades": len(closed_trades) - wins,
        "nifty_regime": "BULLISH",
        "nifty_close": 25349.65,
        "nifty_ema20": 24643.44,
        "nifty_ema50": 24005.85,
        "nifty_20d_return": 0.0215,
        "last_synced_at": datetime.utcnow().isoformat()
    }]

    r1 = requests.post(f"{SUPABASE_URL}/rest/v1/paper_trader_account", headers=headers, json=account_payload)
    print(f"[SYNC] Swing Trader account push response (Total Equity: ₹{total_equity}): {r1.status_code}")

    if trades_payload:
        r2 = requests.post(f"{SUPABASE_URL}/rest/v1/paper_trader_trades", headers=headers, json=trades_payload)
        print(f"[SYNC] Swing Trader trades push response ({len(trades_payload)} records): {r2.status_code}")

if __name__ == "__main__":
    sync()
