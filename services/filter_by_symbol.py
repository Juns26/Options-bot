# services/filter_by_symbol.py
"""
Filter Service: Symbol

Filters a list of raw trade records by the 'symbol' column.
Valid values in the data: 'AAPL', 'GOOGL', 'MSFT', 'META', etc.

Usage:
    from services.filter_by_symbol import filter_by_symbol
    from services.gsheet_service import get_all_trades

    records = get_all_trades()
    aapl_trades = filter_by_symbol(records, "AAPL")
"""

from typing import List, Dict, Any


def filter_by_symbol(records: List[Dict[str, Any]], symbol: str) -> List[Dict[str, Any]]:
    """
    Filters a list of trade records by the 'symbol' field.

    Args:
        records: Raw trade records (e.g. from get_all_trades()).
        symbol:  Symbol value to keep. Case-insensitive. Typically 'AAPL', 'GOOGL', 'MSFT', 'META', etc.

    Returns:
        A filtered list of trade records matching the given symbol.
    """
    target = symbol.strip().lower()
    return [r for r in records if str(r.get("symbol", "")).strip().lower() == target]
