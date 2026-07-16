"""SQLite setup, schema, and seed data."""
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "expenses.db"
STATEMENTS_DIR = DATA_DIR / "statements"

SPLIT_TYPES = ("50_50", "100_hers")

DEFAULT_CATEGORIES = [
    "Groceries", "Rent/Mortgage", "Utilities", "Internet", "Dining",
    "Subscriptions", "Transportation", "Household", "Misc.",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    is_default INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS recurring_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    split_type TEXT NOT NULL CHECK (split_type IN ('50_50','100_hers')),
    day_of_month INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    active INTEGER NOT NULL DEFAULT 1,
    start_date TEXT,
    end_date TEXT
);

CREATE TABLE IF NOT EXISTS statement_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uploaded_filename TEXT NOT NULL,
    card_name TEXT,
    statement_period_start TEXT,
    statement_period_end TEXT,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    original_file_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    split_type TEXT NOT NULL CHECK (split_type IN ('50_50','100_hers')),
    is_recurring_instance INTEGER NOT NULL DEFAULT 0,
    recurring_id INTEGER REFERENCES recurring_expenses(id),
    statement_import_id INTEGER REFERENCES statement_imports(id),
    notes TEXT
);

-- One auto-generated instance per recurring rule per calendar month.
CREATE UNIQUE INDEX IF NOT EXISTS idx_expenses_recurring_month
    ON expenses (recurring_id, substr(date, 1, 7)) WHERE recurring_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses (date);

CREATE TABLE IF NOT EXISTS staged_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_import_id INTEGER NOT NULL REFERENCES statement_imports(id),
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    suggested_category_id INTEGER REFERENCES categories(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','imported','discarded'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- "Her Spending" tab: fully isolated from the split tracker (separate tables so
-- this data can never leak into the split math). Shares only `categories`.
CREATE TABLE IF NOT EXISTS wife_recurring_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    day_of_month INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    active INTEGER NOT NULL DEFAULT 1,
    start_date TEXT,
    end_date TEXT
);

CREATE TABLE IF NOT EXISTS wife_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    is_recurring_instance INTEGER NOT NULL DEFAULT 0,
    recurring_id INTEGER REFERENCES wife_recurring_expenses(id),
    notes TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wife_expenses_recurring_month
    ON wife_expenses (recurring_id, substr(date, 1, 7)) WHERE recurring_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wife_expenses_date ON wife_expenses (date);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO categories (name, is_default) VALUES (?, 1)",
            [(name,) for name in DEFAULT_CATEGORIES],
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('billing_cycle', 'calendar_month')"
        )
        conn.commit()
    finally:
        conn.close()
