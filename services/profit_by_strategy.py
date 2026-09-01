# services/profit_by_strategy.py
"""
Aggregation Service: Profit by Strategy

Aggregates total net_profit grouped by strategy (sum).

Strategy values in data: "BPS", "CSP", "PMCC/CC/LEAPS", "Stk", "Other"
But the service is generic — any strategy value is supported.

Usage:
    from services.profit_by_strategy import profit_by_strategy
    from services.gsheet_service import get_all_trades

    records = get_all_trades()
    breakdown = profit_by_strategy(records)
    # [{"strategy": "CSP", "total_profit": 1234.56, "trade_count": 10, "avg_profit": 123.45}, ...]

    # With date filtering:
    from services.filter_by_date import filter_by_date
    filtered = filter_by_date(records, start_date="2025-01-01", end_date="2025-03-31")
    q1_breakdown = profit_by_strategy(filtered)
"""

from typing import List, Dict, Any
from collections import defaultdict


def profit_by_strategy(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Aggregates sum of net_profit grouped by strategy.

    Args:
        records: Raw trade records (e.g. from get_all_trades() or filtered).

    Returns:
        List of dicts sorted by total_profit descending:
        [
            {"strategy": "CSP", "total_profit": 1234.56, "trade_count": 12, "avg_profit": 102.88},
            {"strategy": "BPS", "total_profit": -200.10, "trade_count": 5, "avg_profit": -40.02},
            ...
        ]
        Returns empty list if records is empty.
    """
    if not records:
        return []

    sums: Dict[str, float] = defaultdict(float)
    counts: Dict[str, int] = defaultdict(int)

    for r in records:
        # Strategy field may be missing/empty — bucket as "Unknown"
        strat = str(r.get("strategy", "")).strip()
        if not strat or strat.lower() == "nan" or strat.lower() == "none":
            strat = "Unknown"

        # net_profit is numeric in gsheet_service, but be defensive
        raw_profit = r.get("net_profit", 0)
        try:
            # Handle strings like "$1,234.56" or "1,234"
            if isinstance(raw_profit, str):
                raw_profit = raw_profit.replace("$", "").replace(",", "").strip()
                if raw_profit in ("", "-", "nan", "None"):
                    profit = 0.0
                else:
                    profit = float(raw_profit)
            else:
                profit = float(raw_profit) if raw_profit is not None else 0.0
        except (ValueError, TypeError):
            profit = 0.0

        sums[strat] += profit
        counts[strat] += 1

    result: List[Dict[str, Any]] = []
    for strat, total in sums.items():
        cnt = counts[strat]
        avg = total / cnt if cnt else 0.0
        result.append(
            {
                "strategy": strat,
                "total_profit": round(total, 2),
                "trade_count": cnt,
                "avg_profit": round(avg, 2),
            }
        )

    # Sort by total_profit descending (most profitable first)
    result.sort(key=lambda x: x["total_profit"], reverse=True)

    return result
