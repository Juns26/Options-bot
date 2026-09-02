# tools/gsheet_tools.py
"""
Google Sheets Tools

LangChain @tool wrappers that call the service layer.
Each tool is a thin wrapper — no data logic here.
Services remain composable; tools chain them internally.

To add a new tool:
  1. Add a function to services/
  2. Add a new @tool wrapper below that chains services
  3. Import the new tool in agent.py and add it to ALL_TOOLS
"""

from typing import List, Dict, Any, Optional
from langchain_core.tools import tool

from services.gsheet_service import get_all_trades
from services.filters_service import (
        filter_by_status,
        filter_by_date,
        filter_by_symbol,
        filter_by_strategy)
from services.aggregation_service import aggregate_trades as _aggregate_trades_service

@tool
def fetch_trades(
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches trade records from Google Sheets with optional filters (AND logic).
    All parameters are optional — omit to fetch all trades; provide only what the query needs.

    Args:
        status: Filter by status — 'Open' or 'Close' (case-insensitive). e.g., status="Open"
        symbol: Filter by ticker — 'META', 'AAPL', 'NVDA', etc. (case-insensitive). e.g., symbol="META"
        strategy: Filter by strategy — 'BPS', 'CSP', 'PMCC/CC/LEAPS' (case-insensitive). e.g., strategy="BPS"
        start_date: Inclusive start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS). e.g., "2025-01-01"
        end_date: Inclusive end date (YYYY-MM-DD). e.g., "2025-01-31"

    Examples:
        fetch_trades() -> all trades
        fetch_trades(status="Open", symbol="META") -> all open META trades
        fetch_trades(symbol="AAPL", start_date="2025-01-01", end_date="2025-03-31") -> AAPL Q1 trades
        fetch_trades(status="Close", start_date="2025-01-01") -> closed trades YTD

    Use this for any filtered fetch — it chains filter_by_status, filter_by_symbol, filter_by_date internally.
    """
    records = get_all_trades()
    if status:
        records = filter_by_status(records, status)
    if symbol:
        records = filter_by_symbol(records, symbol)
    if start_date or end_date:
        records = filter_by_date(records, start_date=start_date, end_date=end_date)
    if strategy:
        records = filter_by_strategy(records, strategy)
    return records


@tool
def aggregate_trades(
    records: List[Dict[str, Any]],
    group_by: Optional[List[str]] = None,
    metric: str = "net_profit",
    agg: str = "sum",
) -> List[Dict[str, Any]]:
    """
    Aggregates trade records by the given group-by columns and metric.

    Args:
        records: Raw trade records (e.g. from fetch_trades()).
        group_by: List of columns to group by. None/[] = overall aggregate (single row).
                  Valid values: symbol, strategy, status, option_type, expiry, action,
                  trade_time:month, trade_time:year, trade_time:week, trade_time:day
        metric: Numeric column to aggregate. Valid: net_profit, premium, fees, collateral.
                Ignored when agg="count".
        agg: Aggregation function. Valid: sum, count, mean/avg, min, max, median.

    Returns:
        List of dicts sorted by aggregated value descending (for sum/count).
        Grouped: [{"symbol":"AAPL", "strategy":"CSP", "value":1234.56, "metric":"net_profit", "agg":"sum", "trade_count":5}, ...]
    """

    return _aggregate_trades_service(records=records, group_by=group_by, metric=metric, agg=agg)