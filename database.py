import sqlite3
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

DB_NAME = "paper_trading.db"

def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: str = DB_NAME) -> None:
    """Initialize SQLite database tables and default account state."""
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
                target REAL NOT NULL,
                exit_date TEXT,
                exit_price REAL,
                pnl REAL,
                status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED_TARGET', 'CLOSED_SL'))
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

def get_account(db_path: str = DB_NAME) -> Dict[str, float]:
    """Fetch the current account details."""
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
            SELECT id, symbol, entry_date, entry_price, qty, stop_loss, target, exit_date, exit_price, pnl, status
            FROM trades
            WHERE status = 'OPEN'
            ORDER BY entry_date ASC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def get_all_trades(db_path: str = DB_NAME) -> List[Dict[str, Any]]:
    """Retrieve all trades."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, symbol, entry_date, entry_price, qty, stop_loss, target, exit_date, exit_price, pnl, status
            FROM trades
            ORDER BY id DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

def record_trade(symbol: str, entry_date: str, entry_price: float, qty: int, stop_loss: float, target: float, db_path: str = DB_NAME) -> int:
    """Record a new open trade."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (symbol, entry_date, entry_price, qty, stop_loss, target, status)
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN')
        """, (symbol, entry_date, entry_price, qty, stop_loss, target))
        conn.commit()
        return cursor.lastrowid

def close_trade(trade_id: int, exit_date: str, exit_price: float, pnl: float, status: str, db_path: str = DB_NAME) -> None:
    """Close an open trade with exit price, exit date, final pnl, and status."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades
            SET exit_date = ?, exit_price = ?, pnl = ?, status = ?
            WHERE id = ?
        """, (exit_date, exit_price, pnl, status, trade_id))
        conn.commit()

if __name__ == "__main__":
    init_db()
    acc = get_account()
    print("Database initialized successfully.")
    print("Account State:", acc)
