"""SQLite storage layer for the shared-account portfolio ledger.

All money values are stored in VND (integers/float), quantities in shares.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB = Path(os.environ.get(
    "PORTFOLIO_DB", Path(__file__).resolve().parent.parent / "data" / "portfolio.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS owners (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE
);

-- Cash movements attributed to a single owner.
-- type: OPENING | DEPOSIT | WITHDRAW | TRANSFER_IN | TRANSFER_OUT
--       | DIVIDEND | INTEREST | FEE | ADJUST
-- amount is SIGNED (positive = cash in to owner's sub-wallet).
CREATE TABLE IF NOT EXISTS cash_tx (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    owner_id  INTEGER NOT NULL REFERENCES owners(id),
    type      TEXT NOT NULL,
    amount    REAL NOT NULL,
    symbol    TEXT,
    group_id  TEXT,
    note      TEXT
);

-- Stock movements attributed to a single owner.
-- side: BUY | SELL | OPEN (opening position, no cash effect)
--       | BONUS (stock dividend / bonus shares, no cash, zero cost)
--       | TRANSFER_IN | TRANSFER_OUT (shares moved between owners at cost)
-- A shared order is stored as several rows with the same group_id.
CREATE TABLE IF NOT EXISTS trades (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    owner_id  INTEGER NOT NULL REFERENCES owners(id),
    symbol    TEXT NOT NULL,
    side      TEXT NOT NULL,
    qty       REAL NOT NULL,
    price     REAL NOT NULL DEFAULT 0,
    fee       REAL NOT NULL DEFAULT 0,
    tax       REAL NOT NULL DEFAULT 0,
    group_id  TEXT,
    note      TEXT
);

CREATE TABLE IF NOT EXISTS corporate_actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ex_date    TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    kind       TEXT NOT NULL,        -- CASH | STOCK
    value      REAL NOT NULL,        -- CASH: VND/share ; STOCK: ratio in % (e.g. 20 = 100:20)
    pay_date   TEXT,
    group_id   TEXT NOT NULL,
    note       TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    close   REAL NOT NULL,
    source  TEXT,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS snapshots (
    date        TEXT NOT NULL,
    owner_id    INTEGER NOT NULL REFERENCES owners(id),
    cash        REAL NOT NULL,
    market_value REAL NOT NULL,
    nav         REAL NOT NULL,
    net_contrib REAL NOT NULL,
    PRIMARY KEY (date, owner_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "fee_rate": "0.0015",       # broker fee 0.15% per side
    "sell_tax_rate": "0.001",   # 0.1% personal income tax on sale value
    "div_tax_rate": "0.05",     # 5% tax on cash dividends
}


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = os.environ.get("PORTFOLIO_DB", DEFAULT_DB)
    is_mem = str(db_path) == ":memory:"
    final_path = Path(db_path) if not is_mem else db_path
    if isinstance(final_path, Path):
        final_path.parent.mkdir(parents=True, exist_ok=True)
    is_default = (not is_mem and Path(final_path) == Path(DEFAULT_DB))
    conn = sqlite3.connect(str(final_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn, seed_if_empty=is_default)
    return conn


def init_db(conn: sqlite3.Connection, seed_if_empty: bool = True) -> None:
    conn.executescript(SCHEMA)
    cur = conn.execute("SELECT COUNT(*) FROM owners")
    if seed_if_empty and cur.fetchone()[0] == 0:
        seed_path = Path(__file__).resolve().parent.parent / "data" / "init_data.sql"
        if seed_path.exists():
            with open(seed_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            conn.commit()

    # Attempt to pull latest changes from Google Sheets if configured
    try:
        from . import gsheet_sync
        gsheet_sync.pull_from_sheets_to_sqlite(conn)
    except Exception as e:
        log.debug("Google Sheets initial pull skipped: %s", e)

    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()


@contextmanager
def tx(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_setting(conn: sqlite3.Connection, key: str, cast=float):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return cast(DEFAULT_SETTINGS[key]) if key in DEFAULT_SETTINGS else None
    return cast(row["value"])


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    with tx(conn):
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    try:
        from . import gsheet_sync
        gsheet_sync.push_table_to_sheet(conn, "settings")
    except Exception:
        pass
