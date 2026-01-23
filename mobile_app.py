# app.py
import streamlit as st
import numpy as np
import plotly.graph_objects as go
from scipy.stats import gaussian_kde
import yfinance as yf

# ----------------------------
# Reuse your existing functions
# ----------------------------

# Estimate parameters from yfinance data
def estimate_params_from_yfinance(ticker_symbol: str, period: str = "5y"):
    try:
        data = yf.download(ticker_symbol, period=period, progress=False)
        if len(data) < 10:
            raise ValueError("Insufficient data")
        log_returns = np.log(data['Close'] / data['Close'].shift(1)).dropna()
        mu = (log_returns.mean() * 252).item()
        sigma = (log_returns.std() * np.sqrt(252)).item()
        S0 = data['Close'].iloc[-1].item()
        return S0, mu, sigma
    except Exception as e:
        st.error(f"❌ Could not fetch data for '{ticker_symbol}': {e}")
        return None, None, None

# Fetch fundamental estimates from yfinance
def get_fundamental_estimates(ticker_symbol: str):
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        
        # Extract available estimates
        estimates = {
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "target_mean": info.get("targetMeanPrice"),
            "target_high": info.get("targetHighPrice"),
            "target_low": info.get("targetLowPrice"),
            "target_median": info.get("targetMedianPrice"),
        }
        print(estimates)
        return estimates
    except Exception as e:
        st.warning(f"⚠️ Could not fetch analyst estimates for {ticker_symbol}: {e}")
        return None

def simulate_stock_price_interactive(
    S0, x_days, mu, sigma, num_simulations, ticker, random_seed=42, estimates = None
):
    np.random.seed(random_seed)
    T = x_days / 252.0
    dt = T / x_days
    Z = np.random.standard_normal((num_simulations, x_days))
    price_paths = np.empty((num_simulations, x_days + 1))
    price_paths[:, 0] = S0

    drift = (mu - 0.5 * sigma**2) * dt
    diffusion = sigma * np.sqrt(dt)
    for t in range(1, x_days + 1):
        price_paths[:, t] = price_paths[:, t - 1] * np.exp(drift + diffusion * Z[:, t - 1])

    final_prices = price_paths[:, -1]
    stats = {
        "S0": S0,
        "x_days": x_days,
        "mu": mu,
        "sigma": sigma,
        "mean": np.mean(final_prices),
        "median": np.median(final_prices),
        "std": np.std(final_prices),
        "p05": np.percentile(final_prices, 5),
        "p95": np.percentile(final_prices, 95),
        "ci_low": np.percentile(final_prices, 2.5),
        "ci_high": np.percentile(final_prices, 97.5),
    }

    days = np.arange(x_days + 1)
    mean_path = np.mean(price_paths, axis=0)
    min_path = np.min(price_paths, axis=0)
    max_path = np.max(price_paths, axis=0)

    # Plot 1: Price Paths
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=np.concatenate([days, days[::-1]]),
        y=np.concatenate([max_path, min_path[::-1]]),
        fill='toself',
        fillcolor='rgba(200, 200, 200, 0.15)',
        line=dict(color='rgba(200,200,200,0)'),
        showlegend=False,
        hoverinfo='skip'
    ))
    fig1.add_trace(go.Scatter(
        x=days,
        y=mean_path,
        mode='lines',
        line=dict(color='#1f77b4', width=3),
        name='Mean Path',
        customdata=np.column_stack([max_path, mean_path, min_path]),
        hovertemplate=(
            'Day: %{x}<br>'
            'Max: $%{customdata[0]:.2f}<br>'
            'Mean: $%{customdata[1]:.2f}<br>'
            'Min: $%{customdata[2]:.2f}<extra></extra>'
        )
    ))

    #add starting price line
    fig1.add_trace(go.Scatter(
        x=[0, x_days],
        y=[S0, S0],
        mode='lines',
        line=dict(color='black', dash='dash', width=2),
        name=f'Start: ${S0:.2f}',
        # showlegend=False,
        hoverinfo='skip'
    ))

    # Overlay fundamental estimates if available
    if estimates:
        colors = {"mean": "green", "median": "blue", "high": "purple", "low": "orange"}
        for key, label in [("target_mean", "Mean Target"), ("target_median", "Median Target"),
                        ("target_high", "High Target"), ("target_low", "Low Target")]:
            price = estimates.get(key)
            if price is not None:
                # Add as a scatter trace with legend (not hline)
                fig1.add_trace(go.Scatter(
                    x=[0, x_days],
                    y=[price, price],
                    mode='lines',
                    line=dict(color=colors.get(key.split('_')[-1], "gray"), dash="dot", width=1.5),
                    name=f"{label}: ${price:.2f}",  # appears in legend
                    showlegend=True,
                    hoverinfo='skip'
                ))

    fig1.update_layout(
        title=f"Monte Carlo Simulation: {ticker} Over {x_days} Days",
        xaxis_title="Days",
        yaxis_title="Stock Price ($)",
        template="plotly_white",
        hovermode="x unified",
        height=500
    )

    # Plot 2: Density
    kde = gaussian_kde(final_prices)
    x_grid = np.linspace(final_prices.min(), final_prices.max(), 500)
    y_density = kde(x_grid)
    p5, p95 = stats["p05"], stats["p95"]

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=x_grid, y=y_density, mode='lines', fill='tozeroy',
                              line=dict(color='#FF6F61'), name='Density', hoverinfo='skip'))
    x_fill = np.linspace(p5, p95, 100)
    y_fill = kde(x_fill)
    fig2.add_trace(go.Scatter(
        x=np.concatenate([x_fill, x_fill[::-1]]),
        y=np.concatenate([y_fill, np.zeros_like(y_fill)]),
        fill='toself', fillcolor='rgba(100,200,100,0.3)', hoverinfo='skip', showlegend=True, name='5%-95% Range'
    ))
    fig2.add_vline(x=p5, line=dict(color='red', dash='dot'), annotation_text=f'5%: ${p5:.2f}', annotation_position="top left")
    fig2.add_vline(x=p95, line=dict(color='purple', dash='dot'), annotation_text=f'95%: ${p95:.2f}', annotation_position="top right")
    fig2.update_layout(
        title=f"Final Price Distribution ({ticker})",
        xaxis_title="Price ($)",
        yaxis_title="Density",
        template="plotly_white",
        height=400
    )

    # Plot 3: Histogram
    bin_edges = np.linspace(final_prices.min(), final_prices.max(), 15)
    counts, _ = np.histogram(final_prices, bins=bin_edges)
    probabilities = counts / counts.sum()
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    fig3 = go.Figure(go.Bar(
        x=bin_centers,
        y=probabilities,
        marker_color='lightblue',
        width=(bin_edges[1] - bin_edges[0]) * 0.9,
        hovertemplate='Price: $%{x:.1f}<br>Prob: %{y:.1%}<extra></extra>'
    ))
    fig3.update_layout(
        title="Probability by Price Bin",
        xaxis_title="Final Price ($)",
        yaxis_title="Probability",
        yaxis=dict(tickformat=".0%"),
        template="plotly_white",
        height=400
    )

    return final_prices, stats, fig1, fig2, fig3

def print_summary_streamlit(stats, ticker):
    s = stats
    st.markdown(f"### 📊 Summary for **{ticker}**")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Current Price (S₀)", f"${s['S0']:.2f}")
        st.metric("Forecast Horizon", f"{s['x_days']} days")
        st.metric("Annual Drift (μ)", f"{s['mu']:.2%}")
        st.metric("Volatility (σ)", f"{s['sigma']:.2%}")
    with col2:
        st.metric("Expected Price (Mean)", f"${s['mean']:.2f}")
        st.metric("Median", f"${s['median']:.2f}")
        st.metric("5% VaR", f"${s['p05']:.2f}")
        st.metric("95% Upside", f"${s['p95']:.2f}")

# ----------------------------
# Streamlit UI
# ----------------------------

st.set_page_config(page_title="📈 Monte Carlo Stock Simulator", layout="wide")
st.title("📈 Monte Carlo Stock Price Simulator")
st.markdown("""
Simulate future stock prices using geometric Brownian motion.  
Enter a ticker, choose historical data length, and run thousands of simulations!
""")

with st.sidebar:
    st.header("⚙️ Parameters")
    ticker = st.text_input("Stock Ticker (e.g., AAPL, MSFT)", value="MSFT").upper()
    # Period selection with custom fallback
    period_options = ["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]
    period_selection = st.selectbox(
        "Historical Data Period",
        options=period_options + ["Other..."],
        index=3  # default to "1y"
    )

    if period_selection == "Other...":
        period = st.text_input(
            "Enter custom period (e.g., '2y', '18mo', '500d')",
            value="1y",
            help="Valid formats: 'Xmo', 'Xy', 'Xd', 'ytd', 'max' (see yfinance docs)"
        )
    else:
        period = period_selection
    x_days = st.slider("Forecast Horizon (Trading Days)", min_value=1, max_value=252, value=30)
    num_sim = st.number_input("Number of Simulations", min_value=100, max_value=200_000, value=10_000, step=1000)
    run = st.button("🚀 Run Simulation")

if run:
    with st.spinner(f"Fetching data for {ticker} ({period})..."):
        S0, mu, sigma = estimate_params_from_yfinance(ticker, period=period)
        estimates = get_fundamental_estimates(ticker)  # ← ADD THIS
    
    if S0 is not None:
        with st.spinner("Running Monte Carlo simulation..."):
            final_prices, stats, fig1, fig2, fig3 = simulate_stock_price_interactive(
                S0=S0, x_days=x_days, mu=mu, sigma=sigma,
                num_simulations=num_sim, ticker=ticker,
                estimates=estimates  # ← PASS IT HERE
            )
        
        print_summary_streamlit(stats, ticker)
        
        st.plotly_chart(fig1, use_container_width=True)
        st.plotly_chart(fig2, use_container_width=True)
        st.plotly_chart(fig3, use_container_width=True)
        
        # Optional: Download raw results
        st.download_button(
            label="📥 Download Final Prices (CSV)",
            data="\n".join(map(str, final_prices)),
            file_name=f"{ticker}_final_prices.csv",
            mime="text/csv"
        )
    else:
        st.error("Failed to load data. Please check the ticker symbol.")