    # services/gsheet_service.py
"""
Google Sheets Service Layer

Handles all raw data access to Google Sheets.
Tools call functions from this module — no data logic lives inside @tool definitions.

To add a new data source (e.g., a different sheet or API), add a new function here
and wire it up in a new @tool in tools/.
"""

import os
from typing import List, Dict, Any, Optional
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread

load_dotenv()

# ==============================================================================
# Configuration
# ==============================================================================

SPREADSHEET_NAME = os.getenv("GOOGLE_SHEETS_SPREADSHEET_NAME", "Options Tracker")
WORKSHEET_NAME = "Tiger Trade API Data"

# Simple in-memory cache to avoid redundant API calls within the same session
_cached_df: Optional[pd.DataFrame] = None
_last_fetch_time: Optional[datetime] = None


# ==============================================================================
# Internal Auth Helper
# ==============================================================================

def _get_gspread_client() -> gspread.Client:
    """Authenticates and returns an authorized gspread client."""
    private_key = os.getenv("GOOGLE_PRIVATE_KEY")
    client_email = os.getenv("GOOGLE_CLIENT_EMAIL")

    if not private_key or not client_email:
        raise ValueError("Google Service Account credentials missing in .env")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(
        {
            "type": "service_account",
            "project_id": os.getenv("GOOGLE_PROJECT_ID"),
            "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"),
            "private_key": private_key.replace("\\n", "\n"),
            "client_email": client_email,
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "auth_uri": os.getenv("GOOGLE_AUTH_URI"),
            "token_uri": os.getenv("GOOGLE_TOKEN_URI"),
            "auth_provider_x509_cert_url": os.getenv("GOOGLE_AUTH_PROVIDER_X509_CERT_URL"),
            "client_x509_cert_url": os.getenv("GOOGLE_CLIENT_X509_CERT_URL"),
            "universe_domain": "googleapis.com",
        },
        scopes=scopes,
    )

    return gspread.authorize(creds)


# ==============================================================================
# Public Service Functions
# ==============================================================================

def get_all_trades(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetches all raw trade records from the Google Sheet.

    Returns a list of dicts, one per trade row, with the following fields:
        action, filled, trade_time, symbol, expiry, option_type,
        combo_type, strike, premium, fees, net_profit, collateral, strategy, status

    Uses an in-memory cache within the same Python session.
    Pass force_refresh=True to bypass the cache and re-fetch from the sheet.
    """
    global _cached_df, _last_fetch_time

    if not force_refresh and _cached_df is not None:
        return _cached_df.to_dict(orient="records")

    client = _get_gspread_client()
    spreadsheet = client.open(SPREADSHEET_NAME)
    sheet = spreadsheet.worksheet(WORKSHEET_NAME)
    all_values = sheet.get_all_values()

    if not all_values or len(all_values) < 3:
        return []

    # Row index 2 (0-based) = row 3 in the sheet = column headers
    # Row index 3+ = trade records
    header_idx = 2
    headers = [h.strip() for h in all_values[header_idx]]
    data_rows = all_values[header_idx + 1:]

    df = pd.DataFrame(data_rows, columns=headers)

    # Drop empty rows
    if "symbol" in df.columns and "trade_time" in df.columns:
        df = df[df["symbol"].str.strip() != ""]
        df = df[df["trade_time"].str.strip() != ""]

    # Coerce numeric columns
    numeric_cols = ["filled", "strike", "premium", "fees", "net_profit", "collateral"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ""), errors="coerce"
            ).fillna(0.0)

    # Strip whitespace from string columns
    str_cols = ["action", "symbol", "expiry", "option_type", "combo_type", "strategy", "status"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    _cached_df = df
    _last_fetch_time = datetime.now()

    return df.to_dict(orient="records")
