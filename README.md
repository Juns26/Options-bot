# ThetaPilot — Agentic Options Portfolio Analyst

Telegram bot that tracks Tiger Brokers options/stock trades in Google Sheets, with a LangGraph `analyze_agent` for natural-language P&L queries and charts. (Formerly: Options Tracker.)

## Features

- **Trade sync**: pulls filled TigerOpen orders, parses single/multi-leg options, computes premium/fees/net, classifies strategy (CSP, BPS, PMCC/CC/LEAPS, Stk), appends to Sheets.
- **`/analyze` agent**: ask `Profit in August 2026 and plot pie by strategy` — plans tool calls, aggregates, renders PNG, replies in Telegram-safe HTML. Open to all users.
- **Classic views (admin)**: MTD / YTD / YoY / Custom performance tables + pie/trend PNGs, positions, monthly targets.
- **Headless charts**: matplotlib `Agg` backend — no Chrome needed on Render/Docker.

## Architecture

### System overview

```mermaid
flowchart LR
    TG["Telegram user"] -->|"commands (/analyze, /performance, ...)"| MAIN["main.py<br/>routing, admin guards, HTML-safe replies"]
    MAIN -->|"read summary ranges"| SHEETS[("Google Sheets<br/>Summary + Data")]
    MAIN -->|"/refresh"| TIGER["fetch_trades.py<br/>TigerOpen chunked sync"]
    TIGER -->|"append rows"| SHEETS
    MAIN -->|"query string"| AGENT["analyze_agent<br/>LangGraph plan-and-execute"]
    AGENT -->|"fetch / aggregate / plot"| SHEETS
    AGENT -->|"PNG dict"| MAIN
```

### analyze_agent graph (LangGraph)

```mermaid
flowchart TD
    START((START)) --> G["guardrail<br/>relevant? trades, P&L, strategies, tickers"]
    G -- "yes" --> P["planner<br/>emit 1-3 tool steps as JSON"]
    G -- "no" --> R["refusal<br/>HTML-safe fallback"]
    P -- "can_fulfill = true" --> E["executor<br/>run steps, resolve $step_N"]
    P -- "can_fulfill = false" --> R
    E --> S["synthesizer<br/>evidence-only HTML, under 180 words"]
    S --> END((END))
    R --> END

    subgraph TOOLS["tools/ (thin @tool wrappers)"]
        T1["fetch_trades"]
        T2["aggregate_trades"]
        T3["plot_trades"]
    end

    subgraph SVC["services/ (pure logic, no LLM)"]
        S1["gsheet_service"]
        S2["filters_service"]
        S3["aggregation_service"]
        S4["plot_service"]
    end

    E -. "calls" .-> TOOLS
    TOOLS -. "delegates to" .-> SVC
```

Graph: `START → guardrail → planner → executor → synthesizer → END` (off-topic or unfulfillable → `refusal`). Built in `build_options_agent_graph()` (`analyze_agent.py`).

| Component | Purpose | Key exports |
|---|---|---|
| `main.py` | Bot routing, admin guards, HTML-safe replies, PNG sending | `handle_analyze`, `_run_analyze_query_and_reply`, `pie/trend_chart_png` via `services.plot_service` |
| `analyze_agent.py` | Plan-and-execute LangGraph graph, sandbox guardrails, Gemini fallback | `guardrail/planner/executor/synthesizer/refusal` nodes, `run_analyze_agent` |
| `tools/gsheet_tools.py` | Thin LangChain `@tool` wrappers (no data logic) | `fetch_trades`, `aggregate_trades`, `plot_trades` |
| `services/gsheet_service.py` | Raw Sheets I/O + in-memory cache | `get_all_trades` |
| `services/filters_service.py` | Pure filters chained by `fetch_trades` | `filter_by_status/symbol/date/strategy`, `filter_exclude_strategy` |
| `services/aggregation_service.py` | Generic group-by (whitelisted cols/metrics) | `aggregate_trades` |
| `services/plot_service.py` | Chart data + PNG renderers | `plot_trades`, `render_chart_png`, `pie/trend_chart_png` |
| `fetch_trades.py` | Broker sync: chunked fetch → `build_trades_dataframe` → Sheets append | `fetch_and_update_trades`, `build_trades_dataframe` |
| `recouncile.py` | Read-only reconcile for a date range, same transform, optional CSV | `fetch_trades_for_period` |
| `sandbox/news_agent.py` | Experimental ticker news (Tavily + Gemini), standalone | `run_news_agent` |

### analyze_agent in detail

Graph: `START → guardrail → planner → executor → synthesizer → END` (off-topic or unfulfillable → `refusal`).

- **guardrail**: relevance check (trades, P&L, strategies, tickers, expiries). Blocks everything else.
- **planner**: emits 1–3 tool steps as JSON (`fetch_trades` → `aggregate_trades`/`plot_trades`). Enforces sandbox rules: only whitelisted params, no manual row math, no live broker actions.
- **executor**: runs steps sequentially, resolves `$step_N` / `records=null` placeholders to prior outputs.
- **synthesizer**: answers from sanitized evidence only (counts + totals + ≤15 agg rows, never full dumps). Output is Telegram HTML (`<b>/<i>/<code>`), ≤180 words: Summary → Key Metrics → Breakdown → Takeaway + chart title.
- **Metric semantics**: `premium` = gross before fees, `fees` = commission+GST, `net_profit` = premium − fees. “Profit less fees / net / after fees” → `net_profit`; “gross / before fees” → `premium`.

Example: `Profit less fees in 2026` → `fetch_trades(start_date="2026-01-01", end_date="2026-12-31")` + `aggregate_trades(metric="net_profit", agg="sum")`.

To add a tool: 1) new function in `services/` 2) `@tool` wrapper in `tools/gsheet_tools.py` 3) add to `ALL_TOOLS` in `analyze_agent.py`.

## Bot commands

- `/analyze <question>` — open to all. e.g. `/analyze Profit by strategy in August 2026 and plot pie`
- `/performance`, `/refresh`, `/get_position`, `/set_target <amount>` — admin only (`TELEGRAM_USER_ID`)
- `/start`, `/help`

## Prerequisites

1. Python 3.10+, Tiger Brokers developer access, Telegram bot token via BotFather.
2. Google service account with Sheets + Drive API; share sheet `Options Tracker` (tabs `Tiger Trade API Summary`, `Tiger Trade API Data`) with the service email.
3. Gemini API key for the agent (`GEMINI_API_KEY`). Tavily key only for `sandbox/news_agent.py`.

```env
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_USER_ID=123456789
GEMINI_API_KEY="..."
# Tiger (legacy client_config.* or TIGER_* both work)
TIGER_ID="..." 
TIGER_ACCOUNT="..."
TIGER_LICENSE="TBSG"
TIGER_PRIVATE_KEY="..."
# Google service account
GOOGLE_PROJECT_ID="..."
GOOGLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
GOOGLE_CLIENT_EMAIL="..."
GOOGLE_CLIENT_ID="..."
GOOGLE_PRIVATE_KEY_ID="..."
GOOGLE_AUTH_URI="https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI="https://oauth2.googleapis.com/token"
GOOGLE_AUTH_PROVIDER_X509_CERT_URL="https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_CLIENT_X509_CERT_URL="..."
```

## Run

```bash
python -m venv venv
# Windows: venv\Scripts\activate / Unix: source venv/bin/activate
pip install -r requirements.txt
python main.py          # bot + :10000/health (or $PORT) for Render
python analyze_agent.py --query "Profit by strategy in August 2026 and plot pie"
python recouncile.py --start 2025-01-01 --end 2025-01-31 --csv out.csv
python sandbox/news_agent.py --ticker NVDA
```

## Notes

- Sheets is the DB: summary tab feeds classic views; raw tab feeds the agent. Cell layout constants live at the top of `main.py`.
- Tiger fetch is chunked (30d) with limit backoff to respect rate limits.
- Agent replies are sanitized to Telegram HTML with plain-text fallback so one bad entity never drops a reply.
