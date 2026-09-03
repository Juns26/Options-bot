from typing import List, Dict, Any, Optional
from datetime import datetime


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


def filter_by_strategy(records: List[Dict[str, Any]], strategy: str) -> List[Dict[str, Any]]:
    """
    Filters a list of trade records by the 'strategy' field (inclusive).

    Args:
        records: Raw trade records (e.g. from get_all_trades()).
        strategy:  Strategy value to keep. Case-insensitive. Typically 'BPS', 'CSP', 'Stk'.

    Returns:
        A filtered list of trade records matching the given strategy.
    """
    target = strategy.strip().lower()
    return [r for r in records if str(r.get("strategy", "")).strip().lower() == target]


def filter_exclude_strategy(records: List[Dict[str, Any]], strategy: str) -> List[Dict[str, Any]]:
    """
    Excludes trade records by the 'strategy' field.

    Args:
        records: Raw trade records (e.g. from get_all_trades()).
        strategy: Strategy value to exclude. Case-insensitive. Use "Stk" to exclude stock trades.

    Returns:
        A filtered list with matching strategy removed. E.g., filter_exclude_strategy(records, "Stk") removes all stock trades.
    """
    target = strategy.strip().lower()
    return [r for r in records if str(r.get("strategy", "")).strip().lower() != target]

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

def filter_by_date(records: List[Dict[str, Any]], start_date: Optional[str] = None, end_date: Optional[str] = None, date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Filter by trade_time inclusive. No pandas. Sample: 'YYYY-MM-DD HH:MM:SS'. Year '2026' = whole year."""
    if date and not start_date and not end_date:
        start_date = end_date = date
    if start_date and not end_date and not date and str(start_date).strip().isdigit() and len(str(start_date).strip()) == 4:
        end_date = start_date
    if not start_date and not end_date or not records:
        return records

    def _dt(v, is_end=False):
        if isinstance(v, datetime):
            return v
        s = str(v).strip()
        if s.isdigit() and len(s) == 4:
            return datetime(int(s), 12, 31, 23, 59, 59, 999999) if is_end else datetime(int(s), 1, 1)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                d = datetime.strptime(s, fmt)
                return d.replace(hour=23, minute=59, second=59, microsecond=999999) if is_end and fmt == "%Y-%m-%d" else d
            except ValueError:
                continue
        raise ValueError(f"Unable to parse date '{v}'")

    s = _dt(start_date) if start_date else None
    e = _dt(end_date, True) if end_date else None
    if s and e and s > e:
        return []

    out = []
    for r in records:
        raw = r.get("trade_time")
        if isinstance(raw, datetime):
            t = raw
        else:
            try:
                t = _dt(raw)
            except ValueError:
                continue
        if s and t < s:
            continue
        if e and t > e:
            continue
        out.append(r)
    return out