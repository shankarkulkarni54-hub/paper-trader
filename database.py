import sqlite3
import os
from typing import List, Dict, Any, Optional

DB_NAME = "paper_trading.db"

def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def migrate_db(db_path: str = DB_NAME) -> None:
    """Migrate trades table if old schema or missing columns are detected."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'")
        row = cursor.fetchone()
        if row:
            sql = row[0]
            if "OPEN_RUNNER" not in sql or "remaining_qty" not in sql:
                cursor.execute("ALTER TABLE trades RENAME TO trades_old")
                cursor.execute("""
                    CREATE TABLE trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        entry_date TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        qty INTEGER NOT NULL,
                        stop_loss REAL NOT NULL,
                        target REAL NOT NULL,
                        exit_date TEXT,
                        exit_price REAL,
                        pnl REAL,
                        status TEXT NOT NULL CHECK (status IN ('OPEN', 'OPEN_RUNNER', 'CLOSED_TARGET', 'CLOSED_SL', 'CLOSED_TRAIL')),
                        initial_qty INTEGER DEFAULT 0,
                        remaining_qty INTEGER DEFAULT 0,
                        target_1 REAL DEFAULT 0.0,
                        target_1_hit INTEGER DEFAULT 0,
                        t1_exit_date TEXT,
                        t1_exit_price REAL,
                        t1_pnl REAL DEFAULT 0.0
                    )
                """)
                cursor.execute("""
                    INSERT INTO trades (
                        id, symbol, entry_date, entry_price, qty, stop_loss, target,
                        exit_date, exit_price, pnl, status, initial_qty, remaining_qty,
                        target_1, target_1_hit, t1_exit_date, t1_exit_price, t1_pnl
                    )
                    SELECT 
                        id, symbol, entry_date, entry_price, qty, stop_loss, target,
                        exit_date, exit_price, pnl, status,
                        qty, qty, target, 0, NULL, NULL, 0.0
                    FROM trades_old
                """)
                cursor.execute("DROP TABLE trades_old")
                conn.commit()

def init_db(db_path: str = DB_NAME) -> None:
    """Initialize SQLite database tables, run schema migrations, and setup account state."""
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
        
        # Trades table with partial exit and trailing support
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                stop_loss REAL NOT NULL,
                target REAL NOT NULL,
                exit_date TEXT,
                exit_price REAL,
                pnl REAL,
                status TEXT NOT NULL CHECK (status IN ('OPEN', 'OPEN_RUNNER', 'CLOSED_TARGET', 'CLOSED_SL', 'CLOSED_TRAIL')),
                initial_qty INTEGER DEFAULT 0,
                remaining_qty INTEGER DEFAULT 0,
                target_1 REAL DEFAULT 0.0,
                target_1_hit INTEGER DEFAULT 0,
                t1_exit_date TEXT,
                t1_exit_price REAL,
                t1_pnl REAL DEFAULT 0.0
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

    # Ensure any existing older schema is upgraded smoothly
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
    """Retrieve all active trades (both full OPEN and OPEN_RUNNER trailing positions)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, symbol, entry_date, entry_price, qty, stop_loss, target,
                   exit_date, exit_price, pnl, status, initial_qty, remaining_qty,
                   target_1, target_1_hit, t1_exit_date, t1_exit_price, t1_pnl
            FROM trades
            WHERE status IN ('OPEN', 'OPEN_RUNNER')
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
                   target_1, target_1_hit, t1_exit_date, t1_exit_price, t1_pnl
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
    target_1: float,
    db_path: str = DB_NAME
) -> int:
    """Record a newly executed swing trade."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                symbol, entry_date, entry_price, qty, stop_loss, target,
                status, initial_qty, remaining_qty, target_1, target_1_hit, t1_pnl
            )
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, 0, 0.0)
        """, (symbol, entry_date, entry_price, qty, stop_loss, target_1, qty, qty, target_1))
        conn.commit()
        return cursor.lastrowid

def record_partial_exit(
    trade_id: int,
    t1_exit_date: str,
    t1_exit_price: float,
    t1_pnl: float,
    remaining_qty: int,
    new_stop_loss: float,
    db_path: str = DB_NAME
) -> None:
    """
    Record Target 1 partial exit (50% lock-in):
    - Sets target_1_hit = 1
    - Records t1_exit_date, t1_exit_price, t1_pnl
    - Updates remaining_qty for runner
    - Moves stop_loss to breakeven (entry_price)
    - Changes status to 'OPEN_RUNNER'
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades
            SET target_1_hit = 1,
                t1_exit_date = ?,
                t1_exit_price = ?,
                t1_pnl = ?,
                remaining_qty = ?,
                stop_loss = ?,
                status = 'OPEN_RUNNER'
            WHERE id = ?
        """, (t1_exit_date, t1_exit_price, t1_pnl, remaining_qty, new_stop_loss, trade_id))
        conn.commit()

def close_trade(
    trade_id: int,
    exit_date: str,
    exit_price: float,
    pnl: float,
    status: str,
    db_path: str = DB_NAME
) -> None:
    """Finalize closure of a trade (Stop Loss or runner trailing stop)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades
            SET exit_date = ?,
                exit_price = ?,
                pnl = ?,
                remaining_qty = 0,
                status = ?
            WHERE id = ?
        """, (exit_date, exit_price, pnl, status, trade_id))
        conn.commit()

if __name__ == "__main__":
    init_db()
    acc = get_account()
    open_trades = get_open_trades()
    print("Database upgraded & initialized successfully.")
    print("Account State:", acc)
    print(f"Active Trades Count: {len(open_trades)}")
