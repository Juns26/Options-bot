# services/filter_by_status.py
"""
Filter Service: Status

Filters a list of raw trade records by the 'status' column.
Valid values in the data: 'Open', 'Close'

Usage:
    from services.filter_by_status import filter_by_status
    from services.gsheet_service import get_all_trades

    records = get_all_trades()
    open_trades = filter_by_status(records, "Open")
"""

from typing import List, Dict, Any


def filter_by_status(records: List[Dict[str, Any]], status: str) -> List[Dict[str, Any]]:
    """
    Filters a list of trade records by the 'status' field.

    Args:
        records: Raw trade records (e.g. from get_all_trades()).
        status:  Status value to keep. Case-insensitive. Typically 'Open' or 'Close'.

    Returns:
        A filtered list of trade records matching the given status.
    """
    target = status.strip().lower()
    return [r for r in records if str(r.get("status", "")).strip().lower() == target]