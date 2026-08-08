# reconciliation.py
"""
Fetch trades for an arbitrary date range and run them through the same
data transformation as fetch_trade.py, WITHOUT touching Google Sheets at all
(no reading last_update, no writing back new rows or timestamps).

Useful for reconciling a specific period against your broker statement,
spot-checking historical trades, or re-deriving a range you already have
in the sheet.

Usage:
    python reconciliation.py --start 2025-01-01 --end 2025-01-31
    python reconciliation.py --start 2025-01-01 --end 2025-01-31 --csv out.csv

Or import and call fetch_trades_for_period() directly from another script.
"""
import os
import argparse
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

from fetch_trades import (
    fetch_orders_in_chunks,
    build_trades_dataframe,
    TIGER_AVAILABLE,
)

if TIGER_AVAILABLE:
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.trade.trade_client import TradeClient

SGT = ZoneInfo("Asia/Singapore")


def get_client_config():
    """Same TigerOpen client config loading as fetch_trade.py's main()."""
    load_dotenv()
    client_config = TigerOpenClientConfig()
    client_config.private_key = os.getenv("client_config.private_key")
    client_config.tiger_id = os.getenv("client_config.tiger_id")
    client_config.account = os.getenv("client_config.account")
    client_config.license = os.getenv("client_config.license")
    return client_config


def parse_date_arg(date_str, end_of_day=False):
    """Parse a 'YYYY-MM-DD' string into a tz-aware SGT datetime.

    end_of_day=True pushes the time to 23:59:59 so an --end date is
    inclusive of that whole day.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt + timedelta(hours=23, minutes=59, seconds=59)
    return dt.replace(tzinfo=SGT)


def fetch_trades_for_period(client_config, start_date, end_date):
    """
    Fetch and transform trades for an explicit [start_date, end_date] window.

    Does NOT read or write any Google Sheet — it purely fetches from Tiger
    and runs the same transformation as fetch_and_update_trades(), returning
    the resulting DataFrame to the caller.

    start_date / end_date: tz-aware datetime objects (SGT recommended).

    Returns (success: bool, result: pd.DataFrame | str)
    On success, result is the transformed trades DataFrame (may be empty).
    On failure, result is an error message string.
    """
    if not TIGER_AVAILABLE:
        return False, "TigerOpen modules not available"

    if not client_config:
        return False, "Failed to get TigerBroker configuration"

    try:
        trade_client = TradeClient(client_config)

        print(f"Fetching trades from {start_date} to {end_date}")
        all_orders = fetch_orders_in_chunks(
            trade_client,
            start_date=start_date,
            end_date=end_date,
            chunk_days=30
        )

        if not all_orders:
            print("No filled orders found in this period.")
            return True, pd.DataFrame()

        final_df = build_trades_dataframe(all_orders)
        print(f"Reconciled {len(final_df)} trade rows for the period")

        return True, final_df

    except Exception as e:
        print(f"Error fetching trades: {e}")
        return False, f"Error: {str(e)}"


def main():
    parser = argparse.ArgumentParser(description="Reconcile trades over a date range (read-only, no gsheet writes).")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--csv", default=None, help="Optional path to save results as CSV")
    args = parser.parse_args()

    load_dotenv()

    start_date = parse_date_arg(args.start)
    end_date = parse_date_arg(args.end, end_of_day=True)

    client_config = get_client_config()
    success, result = fetch_trades_for_period(client_config, start_date, end_date)

    if not success:
        print(f"❌ {result}")
        return

    final_df = result
    if final_df.empty:
        print("✅ No trades found for this period.")
        return

    print(f"✅ Retrieved {len(final_df)} trade rows")
    print(final_df.to_string(index=False))

    if args.csv:
        final_df.to_csv(args.csv, index=False)
        print(f"Saved to {args.csv}")


if __name__ == '__main__':
    main()