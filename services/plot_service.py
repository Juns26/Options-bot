# services/plot_service.py
"""
Plot Service: chart data + Chrome-free PNG rendering (matplotlib).

Single generic primitive that turns (filtered) trade records into chart
data, plus PNG renderers for Telegram. Mirrors aggregation_service
pattern: whitelisted params, no I/O, pure functions.

Why matplotlib over Plotly/kaleido?
- kaleido's `fig.to_image()` requires Google Chrome (`plotly_get_chrome`),
  which Render does not provide -> every chart crashed with
  "Kaleido requires Google Chrome to be installed."
- matplotlib's Agg backend is headless: no Chrome, no display server.
  Works on Render, Docker, and local dev alike.

Usage:
    from services.plot_service import plot_trades, render_chart_png

    records = get_all_trades()
    # Chart data (JSON-serializable, for the LangGraph tool + synthesizer):
    result = plot_trades(records, chart_type="pie", group_by=["strategy"],
                         metric="net_profit", agg="sum", title="Net Profit by Strategy")
    # Telegram PNG photo:
    buf = render_chart_png(result)
    await update.message.reply_photo(photo=buf, caption=result["title"])

    # Direct PNGs for /performance views:
    buf = pie_chart_png({"CSP": 3679.26, "BPS": 38.64}, "Aug 2026 Performance")
    buf = trend_chart_png(periods, profits, profits_with_stk, "2-Month")
"""

import io
import logging
from typing import List, Dict, Any, Optional

import matplotlib

matplotlib.use("Agg")  # headless — must run before pyplot import
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

logger = logging.getLogger(__name__)

# Validated inputs — prevents LLM hallucination (same philosophy as aggregation_service)
VALID_CHARTS = {"bar", "pie", "line", "scatter"}
VALID_GROUPS = {
    "symbol", "strategy", "status", "option_type", "expiry", "action",
    "trade_time:month", "trade_time:year", "trade_time:week", "trade_time:day",
}
VALID_METRICS = {"net_profit", "premium", "fees", "collateral"}
VALID_AGGS = {"sum", "count", "mean", "avg", "min", "max", "median"}

# Minimal, clean theme
COLOR_SEQ = ["#636EFA", "#00CC96", "#EF553B", "#AB63FA", "#FFA15A", "#19D3F3", "#FF6692", "#B6E880"]
MONEY_METRICS = {"net_profit", "premium", "fees", "collateral"}


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
    Generic chart data from trade records.

    Args:
        records: Trade records (e.g. from fetch_trades()).
        chart_type: bar | pie | line | scatter
        group_by: Columns to group by (x-axis). None/[] = single value chart.
                  Valid: symbol, strategy, status, trade_time:month, etc.
        metric: Numeric column to plot. Valid: net_profit, premium, fees, collateral.
        agg: Aggregation: sum, count, mean/avg, min, max, median.
        title: Optional title. Auto-generated if None.

    Returns:
        Dict with `type` ("chart"), `aggregated_data` (list from
        aggregation_service), `chart_type`, `title`, `metric`, `agg` — all
        JSON serializable for tool output. `aggregated_data` gives the LLM
        exact numbers to avoid hallucination. Render to PNG via
        `render_chart_png(result)`.

    Raises:
        ValueError: on invalid chart_type / group_by / metric / agg.
    """
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

    # --- aggregate via existing service (single source of truth) ---
    from services.aggregation_service import aggregate_trades

    agg_data = aggregate_trades(records, group_by=group_by, metric=metric, agg=agg_norm)

    # Default title
    if not title:
        g = ", ".join(group_by) if group_by else "Total"
        title = f"{agg_norm.upper()} of {metric} by {g}"

    return {
        "type": "chart",
        "aggregated_data": agg_data,
        "chart_type": chart_type,
        "title": title,
        "metric": metric,
        "agg": agg_norm,
    }


def _finalize(buf: io.BytesIO) -> io.BytesIO:
    buf.seek(0)
    return buf


def _placeholder_png(title: str, message: str = "No data available") -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.axis("off")
    ax.text(0.5, 0.55, message, ha="center", va="center", fontsize=14)
    ax.set_title(title or "", fontsize=14, pad=12)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return _finalize(buf)


def pie_chart_png(strategy_data: Dict[str, float], title: str) -> io.BytesIO:
    """Strategy-breakdown pie PNG (MTD/YTD views). Sizes use abs(), legend shows signed $."""
    try:
        if not strategy_data or sum(strategy_data.values()) == 0:
            return _placeholder_png(
                f"{title}\nNet Total: $0.00",
                "No performance data found\nTrades will appear here once recorded",
            )
        strategies = list(strategy_data.keys())
        profits = list(strategy_data.values())
        total = sum(profits)
        abs_vals = [abs(p) for p in profits]

        fig, ax = plt.subplots(figsize=(7, 5))
        wedges, _, _ = ax.pie(
            abs_vals, autopct="%1.1f%%", startangle=90,
            colors=COLOR_SEQ[:len(strategies)], pctdistance=0.75,
            wedgeprops=dict(width=0.4, edgecolor="white"), textprops=dict(fontsize=10),
        )
        ax.legend(wedges, [f"{s}: ${p:,.2f}" for s, p in zip(strategies, profits)],
                  loc="center left", bbox_to_anchor=(1, 0.5), fontsize=10)
        ax.set_title(f"{title}\nNet Total: ${total:,.2f}", fontsize=14, pad=12)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return _finalize(buf)
    except Exception as e:  # never let charting break the text reply
        logger.warning(f"pie_chart_png failed, returning placeholder: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return _placeholder_png(title or "Chart", "Chart unavailable")


def trend_chart_png(
    periods: List[str],
    profits: List[float],
    profits_with_stk: List[float],
    chart_type: str = "Month-over-Month",
) -> io.BytesIO:
    """Grouped bars (profit vs profit w/STK) + cumulative lines PNG. Callers pass newest-first."""
    title = f"{chart_type} Performance Trend"
    try:
        if not periods or not profits or not profits_with_stk:
            return _placeholder_png(title)
        # Reverse to chronological (oldest -> newest)
        periods_rev = periods[::-1]
        profits_rev = profits[::-1]
        profits_with_stk_rev = profits_with_stk[::-1]
        cum, running = [], 0.0
        for v in profits_rev:
            running += v or 0.0
            cum.append(running)
        cum_stk, running = [], 0.0
        for v in profits_with_stk_rev:
            running += v or 0.0
            cum_stk.append(running)
        y_label = 'Annual Profit ($)' if chart_type == "Year-on-Year" else 'Monthly Profit ($)'

        x = list(range(len(periods_rev)))
        w = 0.35
        fig, ax1 = plt.subplots(figsize=(max(8, len(periods_rev) * 0.9), 5))
        b1 = ax1.bar([i - w / 2 for i in x], profits_rev, width=w, label="Profit", color="#7ED957", edgecolor="white")
        b2 = ax1.bar([i + w / 2 for i in x], profits_with_stk_rev, width=w, label="Profit (w/STK)", color="#6EC1E4", edgecolor="white")
        ax1.set_xticks(x)
        ax1.set_xticklabels(periods_rev, fontsize=9)
        ax1.set_ylabel(y_label, fontsize=11)
        ax1.axhline(0, color="black", linewidth=1)
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        try:
            ax1.bar_label(b1, fmt=lambda v: f"${v:,.0f}", fontsize=8, padding=2)
            ax1.bar_label(b2, fmt=lambda v: f"${v:,.0f}", fontsize=8, padding=2)
        except Exception:
            pass

        ax2 = ax1.twinx()
        ax2.plot(x, cum, color="#1B7A3D", linestyle="--", marker="o", linewidth=2, label="Cumulative Profit")
        ax2.plot(x, cum_stk, color="#1E3A8A", linestyle="--", marker="s", linewidth=2, label="Cumulative (w/STK)")
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper center",
                   bbox_to_anchor=(0.5, -0.18), ncol=2, fontsize=9)
        ax1.set_title(title, fontsize=14, pad=12)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return _finalize(buf)
    except Exception as e:
        logger.warning(f"trend_chart_png failed, returning placeholder: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return _placeholder_png(title, "Chart unavailable")


def _agent_xy(aggregated_data: List[Dict[str, Any]]):
    """Derive (x_labels, y_values) from aggregation rows.

    Rows look like {"strategy": "CSP", "value": 3679.26, "trade_count": 76}.
    x = first non-meta key (joined if several); y = value.
    """
    meta = {"value", "metric", "agg", "trade_count", "abs_value", "x", "label"}
    xs, ys = [], []
    for row in aggregated_data or []:
        if not isinstance(row, dict):
            continue
        keys = [k for k in row.keys() if k not in meta]
        xs.append(" | ".join(str(row[k]) for k in keys) if keys else "Total")
        try:
            ys.append(float(row.get("value", 0) or 0))
        except (TypeError, ValueError):
            ys.append(0.0)
    return xs, ys


def render_chart_png(result: Dict[str, Any]) -> io.BytesIO:
    """Render a `plot_trades` result dict as PNG (matplotlib, no Chrome needed).

    Uses `aggregated_data` (exact numbers) so the image matches the text
    summary. Supports bar / pie / line / scatter.
    """
    title = str(result.get("title") or "Chart")
    chart_type = str(result.get("chart_type") or "bar").lower()
    try:
        xs, ys = _agent_xy(result.get("aggregated_data") or [])
        if not xs:
            return _placeholder_png(title)

        if chart_type == "pie":
            sizes = [abs(v) for v in ys]
            if sum(sizes) == 0:
                sizes = [1.0] * len(xs)
            fig, ax = plt.subplots(figsize=(7, 5))
            wedges, _, _ = ax.pie(
                sizes, autopct="%1.1f%%", startangle=90, colors=COLOR_SEQ,
                pctdistance=0.75, wedgeprops=dict(width=0.4, edgecolor="white"),
                textprops=dict(fontsize=10),
            )
            ax.legend(wedges, [f"{x}: ${y:,.2f}" for x, y in zip(xs, ys)],
                      loc="center left", bbox_to_anchor=(1, 0.5), fontsize=9)
        elif chart_type == "line":
            fig, ax = plt.subplots(figsize=(max(8, len(xs) * 0.8), 5))
            ax.plot(list(range(len(xs))), ys, marker="o", linewidth=2)
            ax.set_xticks(list(range(len(xs))))
            ax.set_xticklabels(xs, rotation=20, ha="right", fontsize=9)
            ax.axhline(0, color="black", linewidth=1)
        else:  # bar / scatter
            fig, ax = plt.subplots(figsize=(max(8, len(xs) * 0.9), 5))
            bars = ax.bar(xs, ys, color=[COLOR_SEQ[i % len(COLOR_SEQ)] for i in range(len(xs))], edgecolor="white")
            ax.set_xticks(list(range(len(xs))))
            ax.set_xticklabels(xs, rotation=20, ha="right", fontsize=9)
            ax.axhline(0, color="black", linewidth=1)
            try:
                ax.bar_label(bars, fmt=lambda v: f"${v:,.0f}", fontsize=8, padding=2)
            except Exception:
                pass
        ax.set_title(title, fontsize=14, pad=12)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        return _finalize(buf)
    except Exception as e:
        logger.warning(f"render_chart_png failed, returning placeholder: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return _placeholder_png(title, "Chart unavailable")
