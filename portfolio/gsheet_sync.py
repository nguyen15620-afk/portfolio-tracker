"""Google Sheets synchronization layer for portfolio database.

Provides automatic two-way persistence:
1. On startup: loads latest tables from Google Sheet into local SQLite.
2. On any transaction (trade, cash, dividend, settings): pushes changed rows/tables to Google Sheet.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any
import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_SPREADSHEET_ID = "1-Q7kdfsfdOnrQ7wDQza4y542C2b8hiEjnnqM8ROq_EA"
SYNC_TABLES = ["owners", "settings", "corporate_actions", "cash_tx", "trades"]


def get_gspread_client():
    """Get authenticated gspread client using Streamlit secrets or env vars."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.warning("gspread or google-auth not installed.")
        return None

    sa_info = None
    # 1. Check streamlit.secrets
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets:
            sa_info = dict(st.secrets["gcp_service_account"])
        elif "service_account" in st.secrets:
            sa_info = dict(st.secrets["service_account"])
    except Exception:
        pass

    # 2. Check environment variable
    if not sa_info:
        env_sa = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
        if env_sa:
            try:
                sa_info = json.loads(env_sa)
            except Exception:
                pass

    # 3. Fallback to local embedded service account (for local development)
    if not sa_info:
        local_key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if local_key_path and os.path.exists(local_key_path):
            with open(local_key_path, "r", encoding="utf-8") as f:
                sa_info = json.load(f)

    if not sa_info:
        root_key = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "service_account.json")
        if os.path.exists(root_key):
            try:
                with open(root_key, "r", encoding="utf-8") as f:
                    sa_info = json.load(f)
            except Exception:
                pass

    if not sa_info:
        log.warning("No Google Service Account credentials found.")
        return None

    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        log.error("Failed to authorize gspread: %s", e)
        return None


def get_spreadsheet():
    """Open target Google Spreadsheet."""
    gc = get_gspread_client()
    if not gc:
        return None
    sheet_id = os.environ.get("PORTFOLIO_GSHEET_ID", DEFAULT_SPREADSHEET_ID)
    try:
        return gc.open_by_key(sheet_id)
    except Exception as e:
        log.error("Failed to open spreadsheet %s: %s", sheet_id, e)
        return None


def is_sync_enabled(conn: sqlite3.Connection | None = None) -> bool:
    """Check if Google Sheets synchronization is enabled."""
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PORTFOLIO_DISABLE_GSHEET_SYNC") == "1":
        return False
    if conn is not None:
        try:
            cur = conn.execute("PRAGMA database_list")
            for r in cur.fetchall():
                f = r[2] if isinstance(r, tuple) else r["file"]
                if f in ("", ":memory:") or (f and "pytest" in f):
                    return False
        except Exception:
            return False
    return True


def pull_from_sheets_to_sqlite(conn: sqlite3.Connection) -> bool:
    """Pull all db_* tables from Google Sheet into SQLite on startup."""
    if not is_sync_enabled(conn):
        return False
    sh = get_spreadsheet()
    if not sh:
        return False

    success = False
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        with conn:
            for tbl in SYNC_TABLES:
                tab_name = f"db_{tbl}"
                try:
                    ws = sh.worksheet(tab_name)
                    data = ws.get_all_values()
                    if not data or len(data) < 2:
                        continue
                    header = data[0]
                    rows = data[1:]
                    df = pd.DataFrame(rows, columns=header)
                    # Clear local table and insert
                    conn.execute(f"DELETE FROM {tbl}")
                    # Convert appropriate types
                    cols = ", ".join(header)
                    placeholders = ", ".join(["?"] * len(header))
                    sql = f"INSERT INTO {tbl} ({cols}) VALUES ({placeholders})"
                    conn.executemany(sql, df.values.tolist())
                    success = True
                    log.info("Pulled %d rows for %s from Google Sheets.", len(df), tbl)
                except Exception as ex:
                    log.warning("Could not pull %s: %s", tab_name, ex)
    except Exception as e:
        log.error("Error pulling data from Google Sheets: %s", e)
        return False
    finally:
        try:
            conn.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass

    # Also pull latest prices into SQLite prices table
    pull_latest_prices_from_sheet(conn, sh)
    return success


def push_latest_prices_to_sheet(conn: sqlite3.Connection) -> bool:
    """Push latest prices to db_latest_prices worksheet on Google Sheets.

    Merges newly fetched/updated prices with any existing prices in the sheet,
    preventing duplicates by (symbol, date) and keeping the newest dates at the top.
    """
    if not is_sync_enabled(conn):
        return False
    sh = get_spreadsheet()
    if not sh:
        return False

    tab_name = "db_latest_prices"
    try:
        # Get current latest prices per symbol from SQLite
        cur = conn.execute("""
            SELECT p.symbol, p.date, p.close, p.source
            FROM prices p
            INNER JOIN (
                SELECT symbol, MAX(date) AS max_date
                FROM prices
                GROUP BY symbol
            ) m ON p.symbol = m.symbol AND p.date = m.max_date
            GROUP BY p.symbol
            ORDER BY p.symbol
        """)
        sqlite_rows = cur.fetchall()
        if not sqlite_rows:
            return False

        # Map of (symbol, date) -> (close, source)
        price_records: dict[tuple[str, str], tuple[float, str]] = {}

        # 1. Read existing rows from worksheet if present
        try:
            ws = sh.worksheet(tab_name)
            existing_data = ws.get_all_values()
            if existing_data and len(existing_data) >= 2:
                hdr = [h.strip().lower() for h in existing_data[0]]
                s_idx = hdr.index("symbol") if "symbol" in hdr else 0
                d_idx = hdr.index("date") if "date" in hdr else 1
                c_idx = hdr.index("close") if "close" in hdr else 2
                src_idx = hdr.index("source") if "source" in hdr else 3
                for r in existing_data[1:]:
                    if len(r) > max(s_idx, d_idx, c_idx):
                        try:
                            sym = r[s_idx].strip().upper()
                            dt = r[d_idx].strip()
                            cl = float(r[c_idx])
                            src = r[src_idx].strip() if len(r) > src_idx else "vnstock"
                            if sym and dt and cl > 0:
                                price_records[(sym, dt)] = (cl, src)
                        except Exception:
                            continue
        except Exception:
            # Worksheet doesn't exist yet, create it below
            ws = None

        # 2. Merge with current latest prices from SQLite
        for r in sqlite_rows:
            sym = str(r["symbol"]).strip().upper()
            dt = str(r["date"]).strip()
            cl = float(r["close"])
            src = str(r["source"] or "vnstock")
            price_records[(sym, dt)] = (cl, src)

        # 3. Format as rows sorted by date DESC, symbol ASC
        sorted_keys = sorted(price_records.keys(), key=lambda x: (x[1], x[0]), reverse=True)
        out_rows = [
            {"symbol": k[0], "date": k[1], "close": price_records[k][0], "source": price_records[k][1]}
            for k in sorted_keys
        ]
        df = pd.DataFrame(out_rows)

        if ws is None:
            ws = sh.add_worksheet(title=tab_name, rows=max(len(df) + 50, 100), cols=10)

        header = ["symbol", "date", "close", "source"]
        data = [header] + df[["symbol", "date", "close", "source"]].astype(str).values.tolist()
        ws.clear()
        ws.update(data)
        log.info("Pushed %d price records to %s on Google Sheets.", len(df), tab_name)
        return True
    except Exception as e:
        log.error("Failed to push latest prices to Google Sheets: %s", e)
        return False


def pull_latest_prices_from_sheet(conn: sqlite3.Connection, sh=None) -> bool:
    """Pull prices from db_latest_prices worksheet and insert into SQLite prices table."""
    if not is_sync_enabled(conn):
        return False
    if sh is None:
        sh = get_spreadsheet()
    if not sh:
        return False

    tab_name = "db_latest_prices"
    try:
        try:
            ws = sh.worksheet(tab_name)
        except Exception:
            return False

        data = ws.get_all_values()
        if not data or len(data) < 2:
            return False

        hdr = [h.strip().lower() for h in data[0]]
        s_idx = hdr.index("symbol") if "symbol" in hdr else 0
        d_idx = hdr.index("date") if "date" in hdr else 1
        c_idx = hdr.index("close") if "close" in hdr else 2
        src_idx = hdr.index("source") if "source" in hdr else 3

        rows_to_insert = []
        for r in data[1:]:
            if len(r) > max(s_idx, d_idx, c_idx):
                try:
                    sym = r[s_idx].strip().upper()
                    dt = r[d_idx].strip()
                    cl = float(r[c_idx])
                    src = r[src_idx].strip() if len(r) > src_idx else "vnstock"
                    if sym and dt and cl > 0:
                        rows_to_insert.append((sym, dt, cl, src))
                except Exception:
                    continue

        if rows_to_insert:
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO prices(symbol, date, close, source) VALUES (?,?,?,?)",
                    rows_to_insert,
                )
            log.info("Pulled %d price records from Google Sheets into SQLite prices.", len(rows_to_insert))
            return True
        return False
    except Exception as e:
        log.error("Failed to pull latest prices from Google Sheets: %s", e)
        return False


def push_table_to_sheet(conn: sqlite3.Connection, table_name: str) -> bool:
    """Push an entire SQLite table to its corresponding db_* worksheet."""
    if not is_sync_enabled(conn):
        return False
    if table_name not in SYNC_TABLES:
        return False
    sh = get_spreadsheet()
    if not sh:
        return False

    tab_name = f"db_{table_name}"
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn).fillna("")
        try:
            ws = sh.worksheet(tab_name)
        except Exception:
            ws = sh.add_worksheet(title=tab_name, rows=max(len(df) + 20, 100), cols=max(len(df.columns) + 5, 15))

        header = df.columns.tolist()
        data = [header] + df.astype(str).values.tolist()
        ws.clear()
        ws.update(data)
        log.info("Pushed %d rows for %s to Google Sheets.", len(df), tab_name)
        return True
    except Exception as e:
        log.error("Failed to push %s to Google Sheets: %s", tab_name, e)
        return False


def push_all_to_sheet(conn: sqlite3.Connection) -> None:
    """Push all sync tables to Google Sheets."""
    for tbl in SYNC_TABLES:
        push_table_to_sheet(conn, tbl)
    push_latest_prices_to_sheet(conn)

