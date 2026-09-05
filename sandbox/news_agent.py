# sandbox/news_agent.py
"""
Basic News Agent (Sandbox / standalone testable).

What it does:
  1. Search news for a given ticker via Tavily free-tier web search (topic="news").
  2. Summarize results into short bullets + return sources separately.

Usage:
  python sandbox/news_agent.py --ticker NVDA
  python sandbox/news_agent.py --ticker AAPL --max-results 5 --days 7
  python sandbox/news_agent.py --ticker TSLA --json   # machine-readable output

As a module:
  from sandbox.news_agent import run_news_agent
  out = run_news_agent("NVDA")
  print(out["summary"])
"""

import os
import sys
import json
import argparse
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Keep in sync with analyze_agent.py FALLBACK_MODELS.
FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-flash-lite-latest"]


def _time_range_for_days(days: int) -> str:
    if days <= 3:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"


def search_ticker_news(
    ticker: str,
    max_results: int = 5,
    days: int = 7,
    query_extra: str = "stock news",
) -> List[Dict[str, Any]]:
    """Search Tavily for recent news about a ticker. Returns normalized articles."""
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY (or TAVILY_API_KEY) is not set in .env")
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker must be non-empty, e.g. 'NVDA'")

    from tavily import TavilyClient

    client = TavilyClient(api_key=TAVILY_API_KEY)
    query = f"{ticker} {query_extra}".strip()
    # free-tier friendly: basic depth, cap results 5-10
    max_results = max(1, min(int(max_results), 10))

    resp = client.search(
        query=query,
        topic="news",
        time_range=_time_range_for_days(days),
        max_results=max_results,
        search_depth="basic",
        include_answer=False,
        include_raw_content=False,
    )
    raw = resp.get("results", []) if isinstance(resp, dict) else []
    articles = []
    for r in raw:
        articles.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": (r.get("content") or "")[:2000],
                "published_date": r.get("published_date", ""),
                "source": r.get("source") or r.get("url", ""),
            }
        )
    return articles


def _call_gemini(prompt: str, max_retries: int = 2) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    last_err = None
    for model in FALLBACK_MODELS:
        for attempt in range(max_retries):
            try:
                res = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.2),
                )
                return res.text
            except Exception as e:
                last_err = e
                msg = str(e)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    time.sleep(1.0)
                    break
                time.sleep(1.0 * (attempt + 1))
                continue
    raise last_err or RuntimeError("Gemini call failed")


def summarize_articles(ticker: str, articles: List[Dict[str, Any]]) -> str:
    """Summarize articles with Gemini; falls back to extractive bullets if no key."""
    if not articles:
        return f"No recent news found for {ticker}."
    if not GEMINI_API_KEY:
        # No-LLM fallback: first sentence of each article
        lines = [f"Top {len(articles)} news for {ticker} (no LLM key, raw snippets):"]
        for i, a in enumerate(articles, 1):
            snippet = (a.get("content") or "").split(".")[0][:220]
            lines.append(f"{i}. {a.get('title','')} — {snippet}. ({a.get('url','')})")
        return "\n".join(lines)

    evidence = "\n\n".join(
        f"[{i}] {a.get('title','')} ({a.get('published_date','')})\n{a.get('content','')[:1200]}"
        for i, a in enumerate(articles, 1)
    )
    prompt = f"""Summarize recent news for ticker {ticker} in 3-5 concise bullets.

Evidence (numbered articles, use ONLY these facts, never invent):
{evidence}

Rules:
- Each bullet: 1-2 sentences, factual, no hype.
- End each bullet with citation like [1], [2] matching the article number.
- Keep total under 150 words. Plain text, no markdown tables.
"""
    try:
        return _call_gemini(prompt).strip()
    except Exception as e:
        return f"(LLM summary failed: {e})\n" + "\n".join(
            f"{i}. {a.get('title','')} ({a.get('url','')})"
            for i, a in enumerate(articles, 1)
        )


def run_news_agent(
    ticker: str,
    max_results: int = 5,
    days: int = 7,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Full pipeline: search -> summarize. Returns dict with summary + sources."""
    t0 = time.time()
    if verbose:
        print(f"🔎 Searching news for {ticker.upper()} (last {days}d, top {max_results})...")
    articles = search_ticker_news(ticker, max_results=max_results, days=days)
    if verbose:
        print(f"   Found {len(articles)} articles.")
        for a in articles:
            print(f"   - {a['title'][:80]} ({a['published_date']})")
        print("✍️  Summarizing...")
    summary = summarize_articles(ticker.upper(), articles)
    sources = [
        {"title": a["title"], "url": a["url"], "published_date": a.get("published_date", "")}
        for a in articles
    ]
    if verbose:
        print("\n" + summary + "\n")
        print(f"Done in {time.time()-t0:.1f}s.")
    return {
        "ticker": ticker.upper(),
        "summary": summary,
        "sources": sources,
        "article_count": len(articles),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def main():
    p = argparse.ArgumentParser(description="Sandbox News Agent (Tavily + Gemini)")
    p.add_argument("--ticker", "-t", default=None, help="Ticker, e.g. NVDA")
    p.add_argument("--max-results", type=int, default=5, help="1-10, default 5 (free-tier friendly)")
    p.add_argument("--days", type=int, default=7, help="Lookback window in days, default 7")
    p.add_argument("--json", action="store_true", help="Print raw JSON output")
    p.add_argument("--quiet", action="store_true", help="Suppress progress logs")
    args = p.parse_args()

    ticker = args.ticker or (input("Ticker > ").strip() if sys.stdin.isatty() else None)
    if not ticker:
        p.print_help()
        sys.exit(1)

    out = run_news_agent(ticker, max_results=args.max_results, days=args.days, verbose=not args.quiet)
    if args.json:
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
