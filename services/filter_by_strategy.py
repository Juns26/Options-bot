# services/filter_by_strategy.py
"""
Filter Service: Strategy

Filters a list of raw trade records by the 'strategy' column.
Valid values in the data: 'BPS', 'CSP',"PMCC/CC/LEAPS"

Usage:
    from services.filter_by_status import filter_by_strategy
    from services.gsheet_service import get_all_trades

    records = get_all_trades()
    bps_trades = filter_by_strategy(records, "BPS")
"""

from typing import List, Dict, Any

def filter_by_strategy(records: List[Dict[str, Any]], strategy: str) -> List[Dict[str, Any]]:
    """
    Filters a list of trade records by the 'strategy' field.

    Args:
        records: Raw trade records (e.g. from get_all_trades()).
        strategy:  Strategy value to keep. Case-insensitive. Typically 'BPS' or 'CSP'.

    Returns:
        A filtered list of trade records matching the given strategy.
    """
    target = strategy.strip().lower()
    return [r for r in records if str(r.get("strategy", "")).strip().lower() == target]