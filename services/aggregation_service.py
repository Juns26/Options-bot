# services/aggregation_service.py
"""
Aggregation Service: Generic

Performs group-by aggregation over trade records (pure pandas, no I/O).
Mirrors fetch_trades pattern: single generic primitive with whitelisted params.

Usage:
    from services.aggregation_service import aggregate_trades
    from services.gsheet_service import get_all_trades

    records = get_all_trades()
    # Sum net_profit by strategy
    aggregate_trades(records, group_by=["strategy"], metric="net_profit", agg="sum")
    # Count trades per symbol
    aggregate_trades(records, group_by=["symbol"], metric="net_profit", agg="count")
    # Avg premium per month (bucket trade_time)
    aggregate_trades(records, group_by=["trade_time:month"], metric="premium", agg="mean")
    # Overall total (no grouping)
    aggregate_trades(records, group_by=None, metric="net_profit", agg="sum")
"""

from typing import List, Dict, Any, Optional
import pandas as pd

# Whitelisted group-by columns — prevents LLM hallucination
# trade_time:* are derived buckets, not raw columns
VALID_GROUPS = {
    "symbol",
    "strategy",
    "status",
    "option_type",
    "expiry",
    "action",
    "trade_time:month",  # YYYY-MM -> 2025-01
    "trade_time:year",   # YYYY -> 2025
    "trade_time:week",   # YYYY-Www
    "trade_time:day",    # YYYY-MM-DD
}

# Metrics that can be aggregated (numeric columns in gsheet_service)
# Note: no "filled" — user confirmed not present
VALID_METRICS = {
    "net_profit",
    "premium",
    "fees",
    "collateral",
}

VALID_AGGS = {"sum", "count", "mean", "avg", "min", "max", "median"}


def _normalize_agg(agg: str) -> str:
    agg = agg.strip().lower()
    if agg == "avg":
        return "mean"
    return agg


def aggregate_trades(
    records: List[Dict[str, Any]],
    group_by: Optional[List[str]] = None,
    metric: str = "net_profit",
    agg: str = "sum",
) -> List[Dict[str, Any]]:
    """
    Generic aggregation over trade records.

    Args:
        records: Raw trade records (e.g. from get_all_trades() or filtered).
        group_by: List of columns to group by. None/[] = overall aggregate (single row).
                  Valid values: symbol, strategy, status, option_type, expiry, action,
                  trade_time:month, trade_time:year, trade_time:week, trade_time:day
        metric: Numeric column to aggregate. Valid: net_profit, premium, fees, collateral, strike.
                Ignored when agg="count".
        agg: Aggregation function. Valid: sum, count, mean/avg, min, max, median.

    Returns:
        List of dicts sorted by aggregated value descending (for sum/count).
        Grouped: [{"symbol":"AAPL", "strategy":"CSP", "value":1234.56, "metric":"net_profit", "agg":"sum", "trade_count":5}, ...]
        Ungrouped: [{"value":1234.56, "metric":"net_profit", "agg":"sum", "trade_count":10}]
        Returns [] if records empty.
        Always includes trade_count for context even when agg != count.
    """
    if not records:
        return []

    # Normalize and validate
    if group_by is None:
        group_by = []
    if isinstance(group_by, str):
        # LLM sometimes passes single string instead of list
        group_by = [group_by]

    group_by = [g.strip() for g in group_by if g and str(g).strip()]
    agg_norm = _normalize_agg(agg)

    # Validate
    invalid_groups = [g for g in group_by if g not in VALID_GROUPS]
    if invalid_groups:
        raise ValueError(
            f"Invalid group_by {invalid_groups}. Valid: {sorted(VALID_GROUPS)}"
        )
    if agg_norm not in VALID_AGGS:
        raise ValueError(f"Invalid agg '{agg}'. Valid: {sorted(VALID_AGGS)}")
    if agg_norm != "count" and metric not in VALID_METRICS:
        raise ValueError(f"Invalid metric '{metric}'. Valid: {sorted(VALID_METRICS)}")

    df = pd.DataFrame(records)

    # Derive trade_time buckets if needed
    derived_cols = {}
    for g in group_by:
        if g.startswith("trade_time:"):
            if "trade_time" not in df.columns:
                raise ValueError("trade_time column missing for time bucket aggregation")
            # Parse trade_time once per bucket type
            bucket = g.split(":", 1)[1]
            # Coerce to datetime
            ts = pd.to_datetime(df["trade_time"], errors="coerce")
            if bucket == "month":
                derived = ts.dt.to_period("M").astype(str)  # 2025-01, NaT -> "NaT"
                derived = derived.replace("NaT", "Unknown")
            elif bucket == "year":
                derived = ts.dt.year.astype("string").replace("<NA>", "Unknown")
                derived = derived.replace("NaT", "Unknown")
            elif bucket == "week":
                # ISO week: YYYY-Www
                year = ts.dt.isocalendar().year.astype("string")
                week = ts.dt.isocalendar().week.astype("string").str.zfill(2)
                derived = year + "-W" + week
                derived = derived.where(ts.notna(), "Unknown")
            elif bucket == "day":
                derived = ts.dt.date.astype(str).replace("NaT", "Unknown")
            else:
                raise ValueError(f"Unknown trade_time bucket '{bucket}'")
            # Fill unknowns
            derived = derived.fillna("Unknown")
            col_name = g  # keep "trade_time:month" as column name for output
            df[col_name] = derived
            derived_cols[g] = col_name

    # Ensure group columns exist and fill NaN
    for g in group_by:
        if g not in df.columns:
            df[g] = "Unknown"
        else:
            # Normalize string groups: strip, fill empty
            if df[g].dtype == object:
                df[g] = df[g].astype(str).str.strip().replace("", "Unknown").replace("nan", "Unknown").replace("None", "Unknown").replace("NaT", "Unknown")
            df[g] = df[g].fillna("Unknown")

    # Prepare metric column for numeric aggs
    if agg_norm != "count":
        if metric not in df.columns:
            df[metric] = 0.0
        # Coerce to numeric (handle "$1,234.56", ",")
        df[metric] = (
            df[metric].astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False).str.strip()
        )
        df[metric] = pd.to_numeric(df[metric], errors="coerce").fillna(0.0)

    # Overall (no grouping)
    if not group_by:
        trade_count = len(df)
        if agg_norm == "count":
            value = float(trade_count)
        elif agg_norm == "sum":
            value = float(df[metric].sum())
        elif agg_norm in ("mean",):
            value = float(df[metric].mean()) if trade_count else 0.0
        elif agg_norm == "min":
            value = float(df[metric].min()) if trade_count else 0.0
        elif agg_norm == "max":
            value = float(df[metric].max()) if trade_count else 0.0
        elif agg_norm == "median":
            value = float(df[metric].median()) if trade_count else 0.0
        else:
            value = float(df[metric].sum())
        return [
            {
                "value": round(value, 2),
                "metric": metric,
                "agg": agg_norm,
                "trade_count": trade_count,
            }
        ]

    # Grouped
    # Always compute trade_count per group for context
    grouped = df.groupby(group_by, dropna=False)

    # Compute count per group
    counts = grouped.size().reset_index(name="trade_count")

    if agg_norm == "count":
        # For count, value == trade_count
        result_df = counts.copy()
        result_df["value"] = result_df["trade_count"].astype(float)
        result_df["metric"] = metric  # keep for consistency
        result_df["agg"] = agg_norm
    else:
        # Compute requested agg on metric
        if agg_norm == "sum":
            agg_series = grouped[metric].sum().reset_index(name="value")
        elif agg_norm == "mean":
            agg_series = grouped[metric].mean().reset_index(name="value")
        elif agg_norm == "min":
            agg_series = grouped[metric].min().reset_index(name="value")
        elif agg_norm == "max":
            agg_series = grouped[metric].max().reset_index(name="value")
        elif agg_norm == "median":
            agg_series = grouped[metric].median().reset_index(name="value")
        else:
            agg_series = grouped[metric].sum().reset_index(name="value")

        # Merge counts
        result_df = pd.merge(agg_series, counts, on=group_by, how="left")
        result_df["metric"] = metric
        result_df["agg"] = agg_norm

    # Round value
    result_df["value"] = result_df["value"].astype(float).round(2)

    # Sort by value descending for sum/count, ascending for min? Keep desc for sum/count/mean
    # For consistency, sort desc for sum/count, desc for others too (most profitable first)
    result_df = result_df.sort_values(by="value", ascending=False)

    # Convert to list of dicts
    # Ensure group cols are strings and preserve original names
    records_out: List[Dict[str, Any]] = result_df.to_dict(orient="records")

    # Clean up: ensure trade_count int, value float
    for r in records_out:
        r["trade_count"] = int(r["trade_count"])
        r["value"] = float(r["value"])

    return records_out
