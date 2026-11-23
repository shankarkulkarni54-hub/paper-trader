"""
Standalone SQLite to Supabase Cloud Sync for Paper Trader (Strategy V2).
Does not modify bot.py or core trading logic.
Executes at the end of .github/workflows/daily_trade.yml.
"""
import sqlite3
import os
import requests
from datetime import datetime

# Helper to load .env locally if not present in env
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

    # 1. Account sync
    acc = cur.execute("SELECT * FROM account WHERE id = 1").fetchone()
    trades_rows = [dict(r) for r in cur.execute("SELECT * FROM trades").fetchall()]
    conn.close()

    if not acc:
        print("[SYNC] No account record found.")
        return

    open_trades = [t for t in trades_rows if str(t["status"]).startswith("OPEN")]
    closed_trades = [t for t in trades_rows if not str(t["status"]).startswith("OPEN")]

    open_equity = sum(float(t["entry_price"]) * int(t["qty"]) for t in open_trades)
    cash = float(acc["cash"])
    total_equity = cash + open_equity

    wins = sum(1 for t in closed_trades if float(t.get("pnl") or 0) > 0)
    win_rate = round((wins / len(closed_trades)) * 100, 2) if closed_trades else 0.0

    account_payload = [{
        "id": 1,
        "initial_capital": float(acc["initial_capital"]),
        "cash": cash,
        "realized_pnl": float(acc["realized_pnl"]),
        "open_equity": round(open_equity, 2),
        "total_equity": round(total_equity, 2),
        "win_rate": win_rate,
        "total_closed_trades": len(closed_trades),
        "winning_trades": wins,
        "losing_trades": len(closed_trades) - wins,
        "last_synced_at": datetime.utcnow().isoformat()
    }]

    r1 = requests.post(f"{SUPABASE_URL}/rest/v1/paper_trader_account", headers=headers, json=account_payload)
    print(f"[SYNC] Paper Trader account push response: {r1.status_code}")

    # 2. Trades sync
    stage_labels = {0: "INITIAL", 1: "BE", 2: "STAGE_2", 3: "STAGE_3"}
    trades_payload = []
    for t in trades_rows:
        stg = int(t.get("stage") or 0)
        trades_payload.append({
            "id": int(t["id"]),
            "symbol": t["symbol"],
            "entry_date": t["entry_date"],
            "entry_price": float(t["entry_price"]),
            "qty": int(t["qty"]),
            "stop_loss": float(t["stop_loss"]),
            "target": float(t.get("target") or 0.0),
            "exit_date": t.get("exit_date") or None,
            "exit_price": float(t.get("exit_price")) if t.get("exit_price") else None,
            "pnl": float(t.get("pnl")) if t.get("pnl") is not None else None,
            "status": t["status"],
            "highest_close": float(t.get("highest_close") or 0.0),
            "max_r_reached": float(t.get("max_r_reached") or 0.0),
            "stage": stg,
            "stage_label": stage_labels.get(stg, f"STAGE_{stg}"),
            "per_share_risk": float(t.get("per_share_risk") or 0.0),
            "exit_reason": t.get("exit_reason") or "",
            "last_updated": datetime.utcnow().isoformat()
        })

    if trades_payload:
        r2 = requests.post(f"{SUPABASE_URL}/rest/v1/paper_trader_trades", headers=headers, json=trades_payload)
        print(f"[SYNC] Paper Trader trades push response ({len(trades_payload)} records): {r2.status_code}")

if __name__ == "__main__":
    sync()
