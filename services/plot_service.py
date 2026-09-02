# services/plot_service.py
"""
Plot Service: Generic (Plotly)

Single generic primitive that turns (filtered) trade records into a Plotly figure.
Mirrors aggregation_service pattern: whitelisted params, no I/O, pure function.

Why Plotly over Matplotlib?
- Declarative JSON figure (serializable for LangGraph tool output / LLM synthesizer)
- Modern, interactive by default; Matplotlib is imperative + bitmap, hardcoded styling
- Better sandbox fit: no plt.savefig() side-effects, no global state

Usage:
    from services.plot_service import plot_trades
    from services.gsheet_service import get_all_trades

    records = get_all_trades()
    # Pie: profit by strategy
    fig = plot_trades(records, chart_type="pie", group_by=["strategy"], metric="net_profit", agg="sum")
    # Bar: profit by month
    fig = plot_trades(records, chart_type="bar", group_by=["trade_time:month"], metric="net_profit")
    # Line: cumulative would be done via aggregation + line chart
    # Returns Plotly figure dict -> json serializable, or use fig.write_html() / fig.write_image()
"""

from typing import List, Dict, Any, Optional

# Validated inputs — prevents LLM hallucination (same philosophy as aggregation_service)
VALID_CHARTS = {"bar", "pie", "line", "scatter"}
VALID_GROUPS = {
    "symbol", "strategy", "status", "option_type", "expiry", "action",
    "trade_time:month", "trade_time:year", "trade_time:week", "trade_time:day",
}
VALID_METRICS = {"net_profit", "premium", "fees", "collateral"}
VALID_AGGS = {"sum", "count", "mean", "avg", "min", "max", "median"}

# Minimal, clean theme
PLOTLY_TEMPLATE = "plotly_white"
COLOR_SEQ = ["#636EFA", "#00CC96", "#EF553B", "#AB63FA", "#FFA15A", "#19D3F3", "#FF6692", "#B6E880"]


def _normalize_agg(agg: str) -> str:
    agg = agg.strip().lower()
    return "mean" if agg == "avg" else agg


def plot_trades(
    records: List[Dict[str, Any]],
    chart_type: str = "bar",
    group_by: Optional[List[str]] = None,
    metric: str = "net_profit",
    agg: str = "sum",
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generic Plotly chart from trade records.

    Args:
        records: Trade records (e.g. from fetch_trades()).
        chart_type: bar | pie | line | scatter
        group_by: Columns to group by (x-axis). None/[] = single value chart.
                  Valid: symbol, strategy, status, trade_time:month, etc.
        metric: Numeric column to plot. Valid: net_profit, premium, fees, collateral.
        agg: Aggregation: sum, count, mean/avg, min, max, median.
        title: Optional title. Auto-generated if None.

    Returns:
        Dict with `figure` (Plotly figure dict), `aggregated_data` (list from aggregation_service),
        `chart_type` and `title` — all JSON serializable for tool output.
        Render figure via:
            import plotly.graph_objects as go; go.Figure(result["figure"]).write_html("out.html")
            go.Figure(result["figure"]).write_image("out.png")  # needs kaleido
        `aggregated_data` gives LLM exact numbers to avoid hallucination.

    Raises:
        ValueError: on invalid chart_type / group_by / metric / agg.
        ImportError: if plotly not installed.
    """
    try:
        import plotly.express as px
        import plotly.graph_objects as go
    except ImportError as e:
        raise ImportError("plotly not installed. Run: pip install plotly kaleido") from e

    # --- validate (same as aggregation_service) ---
    chart_type = chart_type.strip().lower()
    if chart_type not in VALID_CHARTS:
        raise ValueError(f"Invalid chart_type '{chart_type}'. Valid: {sorted(VALID_CHARTS)}")

    if group_by is None:
        group_by = []
    if isinstance(group_by, str):
        group_by = [group_by]
    group_by = [g.strip() for g in group_by if g and str(g).strip()]

    agg_norm = _normalize_agg(agg)
    bad_groups = [g for g in group_by if g not in VALID_GROUPS]
    if bad_groups:
        raise ValueError(f"Invalid group_by {bad_groups}. Valid: {sorted(VALID_GROUPS)}")
    if agg_norm not in VALID_AGGS:
        raise ValueError(f"Invalid agg '{agg}'. Valid: {sorted(VALID_AGGS)}")
    if agg_norm != "count" and metric not in VALID_METRICS:
        raise ValueError(f"Invalid metric '{metric}'. Valid: {sorted(VALID_METRICS)}")

    if not records:
        # Return empty figure with message (no exception, LLM-friendly)
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_annotation(text="No data to plot", x=0.5, y=0.5, showarrow=False, font=dict(size=16))
        fig.update_layout(title=title or "No data", template=PLOTLY_TEMPLATE)
        return {"figure": fig.to_dict(), "aggregated_data": [], "chart_type": chart_type, "title": title or "No data"}

    # --- aggregate via existing service (single source of truth) ---
    from services.aggregation_service import aggregate_trades

    agg_data = aggregate_trades(records, group_by=group_by, metric=metric, agg=agg_norm)
    if not agg_data:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_annotation(text="No data after aggregation", x=0.5, y=0.5, showarrow=False)
        fig.update_layout(title=title or "No data", template=PLOTLY_TEMPLATE)
        return {"figure": fig.to_dict(), "aggregated_data": [], "chart_type": chart_type, "title": title or "No data"}

    # Default title
    if not title:
        g = ", ".join(group_by) if group_by else "Total"
        title = f"{agg_norm.upper()} of {metric} by {g}"

    # --- build figure ---
    # For single-group pie/bar, x is group_by[0]; for multi-group, join keys
    if not group_by:
        # Single value -> indicator / bar with one value
        val = agg_data[0]["value"]
        if chart_type == "pie":
            fig = px.pie(names=["Total"], values=[abs(val)], title=title, color_discrete_sequence=COLOR_SEQ)
            fig.update_traces(textinfo="label+percent", hovertemplate="%{label}: %{value:.2f}")
        elif chart_type == "line":
            fig = go.Figure(go.Scatter(x=["Total"], y=[val], mode="lines+markers"))
            fig.update_layout(title=title, template=PLOTLY_TEMPLATE)
        else:  # bar / scatter
            fig = px.bar(x=["Total"], y=[val], title=title, labels={"x": "Group", "y": f"{metric} ({agg_norm})"}, color_discrete_sequence=COLOR_SEQ)
    else:
        # Prepare dataframe for Plotly
        import pandas as pd

        df = pd.DataFrame(agg_data)
        # x label: join multiple group_by cols if needed
        if len(group_by) == 1:
            df["x"] = df[group_by[0]].astype(str)
        else:
            df["x"] = df[group_by].astype(str).agg(" | ".join, axis=1)
        # Color by strategy/symbol if present for better UX
        color_col = next((c for c in ["strategy", "symbol", "status"] if c in group_by), None)

        if chart_type == "pie":
            # Pie uses absolute values for sizing but keeps sign in hover
            df["abs_value"] = df["value"].abs()
            fig = px.pie(
                df, names="x", values="abs_value", title=title,
                color="x" if not color_col else None,
                color_discrete_sequence=COLOR_SEQ,
                hover_data={"value": True, "abs_value": False, "trade_count": True},
            )
            fig.update_traces(textinfo="percent+label", hovertemplate="%{label}<br>value=%{customdata[0]:.2f}<br>count=%{customdata[1]}")
        elif chart_type == "bar":
            fig = px.bar(
                df, x="x", y="value", color=color_col, title=title,
                labels={"x": " | ".join(group_by), "value": f"{metric} ({agg_norm})", "trade_count": "Trades"},
                color_discrete_sequence=COLOR_SEQ, hover_data=["trade_count"],
            )
            fig.update_layout(xaxis_tickangle=-20)
        elif chart_type == "line":
            df_sorted = df.sort_values("x") if "trade_time" in "".join(group_by) else df.sort_values("value", ascending=False)
            fig = px.line(
                df_sorted, x="x", y="value", color=color_col, markers=True, title=title,
                labels={"x": " | ".join(group_by), "value": f"{metric} ({agg_norm})"},
                color_discrete_sequence=COLOR_SEQ,
            )
        else:  # scatter
            fig = px.scatter(
                df, x="x", y="value", color=color_col, size="trade_count", title=title,
                labels={"x": " | ".join(group_by), "value": f"{metric} ({agg_norm})"},
                color_discrete_sequence=COLOR_SEQ, hover_data=["trade_count"],
            )

    # Clean layout: no Matplotlib-style hardcoded sizes
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        title=dict(x=0.5, xanchor="center", font=dict(size=18)),
        margin=dict(l=40, r=40, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
        hovermode="closest",
    )
    return {"figure": fig.to_dict(), "aggregated_data": agg_data, "chart_type": chart_type, "title": title}
