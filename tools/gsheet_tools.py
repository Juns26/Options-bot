# tools/gsheet_tools.py
"""
Google Sheets Tools

LangChain @tool wrappers that call the service layer.
Each tool is a thin wrapper — no data logic here.
Services remain composable; tools chain them internally.

To add a new tool:
  1. Add a function to services/
  2. Add a new @tool wrapper below that chains services
  3. Import the new tool in analyze_agent.py and add it to ALL_TOOLS
"""

from typing import List, Dict, Any, Optional
from langchain_core.tools import tool

from services.gsheet_service import get_all_trades
from services.filters_service import (
        filter_by_status,
        filter_by_date,
        filter_by_symbol,
        filter_by_strategy,
        filter_exclude_strategy)
from services.aggregation_service import aggregate_trades as _aggregate_trades_service
from services.plot_service import plot_trades as _plot_trades_service

@tool
def fetch_trades(
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    exclude_strategy: Optional[str] = None,
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
        exclude_strategy: Exclude by strategy — e.g., exclude_strategy="Stk" to exclude stock trades (value labelled "Stk" in sheet, case-insensitive). Use for "excluding stocks", "without stocks", "options only".
        start_date: Inclusive start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS). e.g., "2025-01-01"
        end_date: Inclusive end date (YYYY-MM-DD). e.g., "2025-01-31"

    Examples:
        fetch_trades() -> all trades
        fetch_trades(status="Open", symbol="META") -> all open META trades
        fetch_trades(symbol="AAPL", start_date="2025-01-01", end_date="2025-03-31") -> AAPL Q1 trades
        fetch_trades(status="Close", start_date="2025-01-01") -> closed trades YTD
        fetch_trades(start_date="2026-01-01", end_date="2026-12-31", exclude_strategy="Stk") -> 2026 trades excluding stocks (options only)

    Use this for any filtered fetch — it chains filter_by_status, filter_by_symbol, filter_by_date, filter_by_strategy internally.
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
    if exclude_strategy:
        records = filter_exclude_strategy(records, exclude_strategy)
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

    Metric semantics (critical for correct mapping):
    - premium: Gross cash flow before fees (SELL = +credit, BUY = -debit, x100). Synonyms: gross premium, gross profit, profit before fees.
    - fees: Commission + GST (always >=0, cost).
    - net_profit: Premium less fees (net_profit = premium - fees). Synonyms: net P&L, profit less fees, profit after fees, net profit after fees, P&L net of fees.
      User phrase "profit less fees" or "premium is net profit subtract fees" means net_profit (not premium alone). If user says "premium", use premium; "net" or "less fees" → net_profit.
    - collateral: Margin/collateral amount (for CSP/BPS).

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


@tool
def plot_trades(
    records: List[Dict[str, Any]],
    chart_type: str = "bar",
    group_by: Optional[List[str]] = None,
    metric: str = "net_profit",
    agg: str = "sum",
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates a Plotly chart from trade records.

    Metric semantics: premium = gross before fees, fees = commission+GST, net_profit = premium - fees.
    Use net_profit for "profit less fees / net P&L / after fees"; use premium for "gross / before fees".

    Args:
        records: Trade records (e.g. from fetch_trades()). Must be a list of dicts.
        chart_type: bar | pie | line | scatter
        group_by: Columns to group by (x-axis). None/[] = single value.
                  Valid: symbol, strategy, status, option_type, expiry, action,
                         trade_time:month, trade_time:year, trade_time:week, trade_time:day
        metric: Numeric column. Valid: net_profit, premium, fees, collateral.
        agg: Aggregation: sum, count, mean/avg, min, max, median.
        title: Optional title. Auto-generated if None.

    Returns:
        Plotly figure dict (JSON serializable). Render via:
            import plotly.graph_objects as go; go.Figure(fig).write_html("chart.html")
            go.Figure(fig).write_image("chart.png")  # needs kaleido

    Examples:
        plot_trades(records, "pie", ["strategy"], "net_profit", "sum")
        plot_trades(records, "bar", ["trade_time:month"], "net_profit", "sum")
        plot_trades(records, "bar", ["symbol"], "net_profit", "sum", "Profit by Symbol - Aug 2026")
    """
    return _plot_trades_service(
        records=records, chart_type=chart_type, group_by=group_by, metric=metric, agg=agg, title=title
    )