import sqlite3
import os
from typing import List, Dict, Any, Optional

DB_NAME = "paper_trading.db"

# Strategy V2 3-Stage Trailing Milestones
STAGE_INITIAL = 0
STAGE_BE = 1
STAGE_2 = 2
STAGE_3 = 3

STAGE_LABELS = {
    STAGE_INITIAL: "INITIAL",
    STAGE_BE: "BE",
    STAGE_2: "STAGE_2",
    STAGE_3: "STAGE_3",
}

def get_stage_label(stage: int) -> str:
    """Return institutional stage label for a given integer stage code: INITIAL, BE, STAGE_2, STAGE_3."""
    return STAGE_LABELS.get(stage, f"STAGE_{stage}")


def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def migrate_db(db_path: str = DB_NAME) -> None:
    """Ensure trades table schema supports dynamic ATR Chandelier trailing and stages."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'")
        row = cursor.fetchone()
        if row:
            cursor.execute("PRAGMA table_info(trades)")
            columns = [c[1] for c in cursor.fetchall()]

            # Add missing columns if needed
            new_columns = {
                "highest_close": "REAL DEFAULT 0.0",
                "max_r_reached": "REAL DEFAULT 0.0",
                "stage": "INTEGER DEFAULT 0",
                "per_share_risk": "REAL DEFAULT 0.0",
                "exit_reason": "TEXT DEFAULT ''",
            }
            for col_name, col_type in new_columns.items():
                if col_name not in columns:
                    cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")

            # Backfill initial values for existing open trades if needed
            cursor.execute("""
                UPDATE trades
                SET highest_close = entry_price
                WHERE highest_close IS NULL OR highest_close = 0.0
            """)
            cursor.execute("""
                UPDATE trades
                SET per_share_risk = ROUND(entry_price - stop_loss, 2)
                WHERE per_share_risk IS NULL OR per_share_risk = 0.0
            """)
            conn.commit()

def init_db(db_path: str = DB_NAME) -> None:
    """Initialize SQLite database tables and ensure all columns exist."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Account table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_capital REAL NOT NULL DEFAULT 200000.0,
                cash REAL NOT NULL DEFAULT 200000.0,
                realized_pnl REAL NOT NULL DEFAULT 0.0
            )
        """)
        
        # Trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                stop_loss REAL NOT NULL,
                target REAL DEFAULT 0.0,
                exit_date TEXT,
                exit_price REAL,
                pnl REAL,
                status TEXT NOT NULL,
                initial_qty INTEGER DEFAULT 0,
                remaining_qty INTEGER DEFAULT 0,
                target_1 REAL DEFAULT 0.0,
                target_1_hit INTEGER DEFAULT 0,
                t1_exit_date TEXT,
                t1_exit_price REAL,
                t1_pnl REAL DEFAULT 0.0,
                highest_close REAL DEFAULT 0.0,
                max_r_reached REAL DEFAULT 0.0,
                stage INTEGER DEFAULT 0,
                per_share_risk REAL DEFAULT 0.0,
                exit_reason TEXT DEFAULT ''
            )
        """)
        
        # Initialize default account row if it doesn't exist
        cursor.execute("SELECT COUNT(*) FROM account")
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO account (id, initial_capital, cash, realized_pnl)
                VALUES (1, 200000.0, 200000.0, 0.0)
            """)
        conn.commit()

    migrate_db(db_path)

def get_account(db_path: str = DB_NAME) -> Dict[str, float]:
    """Fetch current account details."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT initial_capital, cash, realized_pnl FROM account WHERE id = 1")
        row = cursor.fetchone()
        if row:
            return {
                "initial_capital": float(row["initial_capital"]),
                "cash": float(row["cash"]),
                "realized_pnl": float(row["realized_pnl"]),
            }
        return {"initial_capital": 200000.0, "cash": 200000.0, "realized_pnl": 0.0}

def update_account(cash: float, realized_pnl: float, db_path: str = DB_NAME) -> None:
    """Update account cash balance and realized PnL."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE account
            SET cash = ?, realized_pnl = ?
            WHERE id = 1
        """, (cash, realized_pnl))
        conn.commit()

def get_open_trades(db_path: str = DB_NAME) -> List[Dict[str, Any]]:
    """Retrieve all trades that are currently OPEN."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, symbol, entry_date, entry_price, qty, stop_loss, target,
                   exit_date, exit_price, pnl, status, initial_qty, remaining_qty,
                   highest_close, max_r_reached, stage, per_share_risk, exit_reason
            FROM trades
            WHERE status LIKE 'OPEN%'
            ORDER BY entry_date ASC, id ASC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_all_trades(db_path: str = DB_NAME) -> List[Dict[str, Any]]:
    """Retrieve all trades."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, symbol, entry_date, entry_price, qty, stop_loss, target,
                   exit_date, exit_price, pnl, status, initial_qty, remaining_qty,
                   highest_close, max_r_reached, stage, per_share_risk, exit_reason
            FROM trades
            ORDER BY id DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def record_trade(
    symbol: str,
    entry_date: str,
    entry_price: float,
    qty: int,
    stop_loss: float,
    per_share_risk: float,
    target: float = 0.0,
    db_path: str = DB_NAME
) -> int:
    """Record a newly executed swing trade."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                symbol, entry_date, entry_price, qty, stop_loss, target,
                status, initial_qty, remaining_qty, highest_close, max_r_reached,
                stage, per_share_risk, exit_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 0.0, 0, ?, '')
        """, (symbol, entry_date, entry_price, qty, stop_loss, target, qty, qty, entry_price, per_share_risk))
        conn.commit()
        return cursor.lastrowid

def update_trade_trailing_stop(
    trade_id: int,
    stop_loss: float,
    highest_close: float,
    max_r_reached: float,
    stage: int,
    db_path: str = DB_NAME
) -> None:
    """Update active position's trailing stop, peak close, max R, and stage."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades
            SET stop_loss = ?,
                highest_close = ?,
                max_r_reached = ?,
                stage = ?
            WHERE id = ?
        """, (stop_loss, highest_close, max_r_reached, stage, trade_id))
        conn.commit()

def close_trade(
    trade_id: int,
    exit_date: str,
    exit_price: float,
    pnl: float,
    status: str,
    exit_reason: str = "",
    db_path: str = DB_NAME
) -> None:
    """Close full position upon trailing stop breach."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades
            SET exit_date = ?,
                exit_price = ?,
                pnl = ?,
                remaining_qty = 0,
                status = ?,
                exit_reason = ?
            WHERE id = ?
        """, (exit_date, exit_price, pnl, status, exit_reason, trade_id))
        conn.commit()

def reset_portfolio(db_path: str = DB_NAME) -> None:
    """Reset paper trading portfolio: wipe all trades and restore 2L initial cash."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trades")
        cursor.execute("""
            UPDATE account
            SET initial_capital = 200000.0,
                cash = 200000.0,
                realized_pnl = 0.0
            WHERE id = 1
        """)
        conn.commit()

if __name__ == "__main__":
    init_db()
    reset_portfolio()
    acc = get_account()
    open_trades = get_open_trades()
    print("Database reset & initialized successfully.")
    print("Account State:", acc)
    print(f"Open Positions: {len(open_trades)}")
