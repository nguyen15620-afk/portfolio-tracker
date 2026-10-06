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


def pull_from_sheets_to_sqlite(conn: sqlite3.Connection) -> bool:
    """Pull all db_* tables from Google Sheet into SQLite on startup."""
    sh = get_spreadsheet()
    if not sh:
        return False

    success = False
    try:
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
    return success


def push_table_to_sheet(conn: sqlite3.Connection, table_name: str) -> bool:
    """Push an entire SQLite table to its corresponding db_* worksheet."""
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
