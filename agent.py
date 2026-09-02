# agent.py
"""
Plan-and-Execute Options Tracker Agent (LangGraph Architecture in Sandbox Mode)

Architecture:
  agent.py         → LangGraph StateGraph ONLY (nodes, routing, CLI, prompts)
  tools/           → Thin @tool wrappers (call services/)
  services/        → Raw data access (Google Sheets, caching, filters)

Graph: START -> guardrail -> (conditional) -> planner -> (conditional) -> executor -> synthesizer -> END
                           -> refusal -^                     -> refusal -^
"""

import os
import sys
import json
import re
import argparse
import time
from typing import Dict, Any, List, TypedDict
from dotenv import load_dotenv

# Fix Windows console encoding for Unicode/Emojis
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from google import genai
from google.genai import types
from langgraph.graph import StateGraph, START, END

# Tools — thin wrappers that delegate to services/
# Single filtered fetch tool (chains filter_by_status/symbol/date/strategy) + generic aggregation
from tools.gsheet_tools import fetch_trades, aggregate_trades

# ==============================================================================
# Configuration
# ==============================================================================

load_dotenv()

FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-flash-lite-latest"]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# All registered tools in the sandbox — keep it minimal: 1 fetch + 1 aggregation
ALL_TOOLS = [fetch_trades, aggregate_trades]
TOOL_MAP = {t.name: t for t in ALL_TOOLS}

# Regex for planner placeholders like "$step_1", "$step_1_result", "step_2.output" - any $step_N prefix
_PLACEHOLDER_RE = re.compile(r"^\$?step[_-]?(\d+)", re.IGNORECASE)


def _resolve_arg(value: Any, execution_results: List[Dict[str, Any]]) -> Any:
    """
    Resolve a single argument value if it is a placeholder for a prior step.

    Logic:
    1. If value is a string like "$step_1" / "step_2" -> return execution_results[N-1]["result"]
       This is how the LLM planner references the output of a previous tool.
    2. Otherwise return value unchanged.
    Returns the original value if no placeholder is detected or index is out of range.
    """
    if isinstance(value, str):
        m = _PLACEHOLDER_RE.match(value.strip())
        if m:
            idx = int(m.group(1)) - 1  # step numbers are 1-indexed, list is 0-indexed
            if 0 <= idx < len(execution_results):
                return execution_results[idx].get("result")
    return value


def _resolve_placeholders(args: Dict[str, Any], execution_results: List[Dict[str, Any]], verbose: bool = False) -> Dict[str, Any]:
    """
    Resolve all placeholder arguments for a tool call.

    Logic:
    1. Iterate over each arg; try to resolve string placeholders via _resolve_arg().
    2. Special case: LLM often emits null/None for `records` meaning "use previous fetch output".
       If `records` is None and we have prior results, replace with the most recent list result
       (preferring the last fetch_trades output, otherwise the immediate predecessor).
    3. Return a new args dict with resolved values; original is not mutated.
    This makes the executor dynamic for any tool/step count (e.g., 2-step fetch->aggregate
    or 3-step fetch->fetch->aggregate referencing $step_1 or $step_2).
    """
    if not execution_results or not args:
        return args
    resolved = {}
    for k, v in args.items():
        # Try generic $step_N resolution first
        new_v = _resolve_arg(v, execution_results)
        if new_v is not v:  # placeholder was resolved (identity check)
            resolved[k] = new_v
            if verbose:
                n = len(new_v) if isinstance(new_v, list) else 1
                print(f"   ↳ Resolved placeholder `{k}={v}` → {n} records from referenced step")
            continue
        # Handle null placeholder for `records` (common LLM pattern)
        if v is None and k == "records":
            target = None
            for prev in reversed(execution_results):
                if isinstance(prev.get("result"), list):
                    target = prev["result"]
                    break
            if target is not None:
                resolved[k] = target
                if verbose:
                    print(f"   ↳ Resolved placeholder `{k}=null` → {len(target)} records from previous step")
                continue
        resolved[k] = v
    return resolved


def get_tools_prompt() -> str:
    """Formats tools for the planner prompt."""
    descriptions = []
    for t in ALL_TOOLS:
        descriptions.append(
            f"- Tool: `{t.name}`\n"
            f"  Description: {t.description}\n"
            f"  Schema: {json.dumps(t.args, indent=2)}"
        )
    return "\n\n".join(descriptions)


# ==============================================================================
# LangGraph State & Node Definitions
# ==============================================================================

class AgentState(TypedDict):
    query: str
    is_relevant: bool
    guardrail_reason: str
    can_fulfill: bool
    sandbox_reason: str
    plan_summary: str
    steps: List[Dict[str, Any]]
    execution_results: List[Dict[str, Any]]
    final_response: str
    verbose: bool


def call_gemini_with_retry(prompt: str, is_json: bool = False, temperature: float = 0.1, max_retries: int = 3) -> str:
    """Calls Gemini API with automatic model fallback and exponential backoff retry for rate limits."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    config = types.GenerateContentConfig(
        response_mime_type="application/json" if is_json else None,
        temperature=temperature
    )

    last_error = None
    for model_name in FALLBACK_MODELS:
        for attempt in range(max_retries):
            try:
                res = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
                return res.text
            except Exception as e:
                last_error = e
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    time.sleep(1.0)
                    break
                elif attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                else:
                    break

    if last_error:
        raise last_error
    raise RuntimeError("Failed to generate response from all available models.")


def guardrail_node(state: AgentState) -> Dict[str, Any]:
    """Node 1: Evaluates whether user query is relevant to options tracker."""
    query = state["query"]
    verbose = state.get("verbose", True)

    if verbose:
        print("\n" + "=" * 70)
        print(f'💬 Query: "{query}"')
        print("=" * 70)
        print("\n🛡️  Step 1 [LangGraph Node: Guardrail]: Checking query relevance...")

    prompt = f"""
You are a security and relevance guardrail for an Options Trading Portfolio Agent.
Evaluate whether the following user query is relevant to:
- Options trading, stock trades, trade history, orders
- Portfolio performance, profit and loss (P&L), win rates, returns, fees
- Trading strategies (CSP, BPS, Covered Calls, PMCC, LEAPS, Spreads, etc.)
- Specific ticker analysis (e.g. BABA, NVDA, FXI, SOFI, MSFT, META, GOOG, AMZN, SPY, QQQ)
- Portfolio positions, expiration dates, strikes, collateral, trade metrics

Query: "{query}"

Respond strictly in JSON:
{{
  "is_relevant": true or false,
  "reason": "Brief explanation of why it is or is not relevant."
}}
"""
    try:
        raw_text = call_gemini_with_retry(prompt, is_json=True, temperature=0.0)
        data = json.loads(raw_text)
        is_rel = data.get("is_relevant", True)
        reason = data.get("reason", "")
    except Exception:
        is_rel = True
        reason = "Guardrail check passed."

    if verbose:
        if is_rel:
            print(f"✅ Guardrail Passed: {reason}")
        else:
            print(f"⛔ Guardrail Triggered: {reason}")

    return {
        "is_relevant": is_rel,
        "guardrail_reason": reason
    }


def planner_node(state: AgentState) -> Dict[str, Any]:
    """Node 2: Generates multi-step tool plan and verifies sandbox limits."""
    query = state["query"]
    verbose = state.get("verbose", True)

    if verbose:
        print("\n📋 Step 2 [LangGraph Node: Planner]: Generating tool plan & checking sandbox limits...")

    tools_prompt = get_tools_prompt()

    prompt = f"""
You are the Planning Engine of an Options Portfolio Assistant operating in Sandbox Mode.
Available tools in your sandbox:
{tools_prompt}

Strict Sandbox Capabilities & Planning Rules:
1. Tool Capability Boundaries: You must ONLY plan actions that match the EXACT schemas and capabilities of the registered tools above.
2. No Manual LLM Filtering or Calculation: If a query asks for specific filtering (e.g. "Year to Date", "YTD", date ranges, specific tickers, strategy filtering, status filtering) or mathematical aggregations/summaries, and there is NO dedicated tool or parameter to perform that filter/calculation, you CANNOT fulfill the query. You MUST NOT fetch raw records and attempt to manually filter or calculate metrics across hundreds of rows yourself.
3. If the required tool or filter parameter is not present in the available tools, set "can_fulfill": false and clearly explain which tool or capability is missing (e.g., "The current sandbox only provides a raw data extraction tool (`fetch_gsheet_trades`) without date filtering, ticker filtering, or aggregation tools").
4. If the query asks for live trading or broker actions, set "can_fulfill": false.

User Query: "{query}"

Respond strictly in JSON:
{{
  "can_fulfill": true or false,
  "reason": "Explanation of your plan or why the query cannot be fulfilled with current tools",
  "plan_summary": "Short 1-sentence summary of the plan",
  "steps": [
    {{
      "step_number": 1,
      "tool_name": "tool_name_here",
      "arguments": {{ ... }},
      "purpose": "Why this tool step is being executed"
    }}
  ]
}}
"""
    try:
        raw_text = call_gemini_with_retry(prompt, is_json=True, temperature=0.1)
        plan = json.loads(raw_text)
        # Guard against LLM returning a bare list (e.g., steps array) instead of dict
        if isinstance(plan, list):
            # Only accept list if it looks like a valid steps array
            if plan and isinstance(plan[0], dict) and "tool_name" in plan[0]:
                steps = plan
                can_fulfill = True
                reason = ""
                plan_summary = f"{len(steps)} steps (list response)"
            else:
                raise ValueError(f"LLM returned unexpected list: {str(plan)[:500]}")
        else:
            can_fulfill = plan.get("can_fulfill", True)
            reason = plan.get("reason", "")
            plan_summary = plan.get("plan_summary", "")
            steps = plan.get("steps", [])
    except Exception as e:
        can_fulfill = False
        reason = f"Planning error: {str(e)}"
        plan_summary = ""
        steps = []

    if verbose:
        if can_fulfill:
            print(f"✅ Plan Created: {plan_summary} ({len(steps)} steps)")
        else:
            print(f"⚠️  Cannot fulfill in sandbox: {reason}")

    return {
        "can_fulfill": can_fulfill,
        "sandbox_reason": reason,
        "plan_summary": plan_summary,
        "steps": steps
    }


def executor_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Executes plan tool steps sequentially."""
    steps = state.get("steps", [])
    verbose = state.get("verbose", True)
    execution_results = []

    if verbose:
        print("\n⚙️  Step 3 [LangGraph Node: Executor]: Executing tool steps...")

    for step in steps:
        step_num = step.get("step_number", 1)
        tool_name = step.get("tool_name")
        args = step.get("arguments", {}) or {}
        purpose = step.get("purpose", "")

        # Resolve placeholders like "$step_1" or null -> actual prior output
        # Keeps executor dynamic for any step count/combination (e.g., fetch->aggregate, fetch->fetch->aggregate)
        args = _resolve_placeholders(args, execution_results, verbose=verbose)

        if verbose:
            # Truncate large records for display
            display_args = {}
            for k, v in args.items():
                if k == "records" and isinstance(v, list):
                    display_args[k] = f"<{len(v)} records>"
                else:
                    display_args[k] = v
            print(f"\n⚙️  [Step {step_num}] Running tool `{tool_name}`: {purpose}")
            print(f"   Args: {json.dumps(display_args)}")

        if tool_name in TOOL_MAP:
            tool_fn = TOOL_MAP[tool_name]
            try:
                result = tool_fn.invoke(args)
            except Exception as e:
                result = {"error": f"Error running {tool_name}: {str(e)}"}
        else:
            result = {"error": f"Tool '{tool_name}' not available in sandbox."}

        execution_results.append({
            "step": step_num,
            "tool_name": tool_name,
            "arguments": args,
            "purpose": purpose,
            "result": result
        })

        if verbose:
            if isinstance(result, list):
                status_summary = f"Records: {len(result)}"
            elif isinstance(result, dict):
                status_summary = f"Records: {result.get('total_records', len(result.get('results', [])))}" if "results" in result or "total_records" in result else "Done"
            else:
                status_summary = "Done"
            print(f"   ✓ Step {step_num} completed ({status_summary})")

    return {"execution_results": execution_results}


def synthesizer_node(state: AgentState) -> Dict[str, Any]:
    """Node 4: Synthesizes final formatted response for user."""
    query = state["query"]
    plan_summary = state.get("plan_summary", "")
    reason = state.get("sandbox_reason", "")
    execution_results = state.get("execution_results", [])
    verbose = state.get("verbose", True)

    if verbose:
        print("\n📊 Step 4 [LangGraph Node: Synthesizer]: Synthesizing presentation...")

    prompt = f"""
You are an expert Options Portfolio Intelligence Assistant.
User Query: "{query}"

Plan Summary: {plan_summary}
Plan Context: {reason}

Tool Execution Outputs:
{json.dumps(execution_results, indent=2, default=str)}

Instructions:
1. Provide a direct, insightful, and clearly structured answer to the user's query.
2. Format numbers nicely (e.g. `$1,234.56`, `78.5% win rate`).
3. Use markdown tables, bullet points, and sections where appropriate for readability.
4. Highlight notable takeaways, win/loss metrics, or top performers if applicable.
5. Maintain professional financial tone.
"""
    try:
        response_text = call_gemini_with_retry(prompt, is_json=False, temperature=0.2)
    except Exception as e:
        response_text = f"Error synthesizing response: {str(e)}"

    if verbose:
        print("\n" + "=" * 70)
        print("📈 Final Response:")
        print("=" * 70 + "\n")

    return {"final_response": response_text}


def refusal_node(state: AgentState) -> Dict[str, Any]:
    """Fallback node when query is off-topic or outside sandbox."""
    if not state.get("is_relevant", True):
        msg = (
            f"🚫 **Query Not Relevant to Options Tracker**\n\n"
            f"{state.get('guardrail_reason', '')}\n\n"
            f"💡 *I am specialized in tracking and analyzing your options trading portfolio, "
            f"historical performance, P&L, strategies (CSP, BPS, Covered Calls, Spreads), "
            f"and ticker analytics from your Google Sheet records.*"
        )
    else:
        msg = (
            f"⚠️ **Cannot Fulfill Request in Sandbox Mode**\n\n"
            f"{state.get('sandbox_reason', '')}\n\n"
            f"💡 *Currently in Sandbox Mode, I have read-only access to your Google Sheet trade data "
            f"and analytical tools. Actions such as executing live broker orders, deleting rows, "
            f"or modifying broker settings are not supported.*"
        )
    return {"final_response": msg}


# ==============================================================================
# LangGraph Graph Assembly & Conditional Routing
# ==============================================================================

def route_guardrail(state: AgentState) -> str:
    """Route after guardrail check."""
    return "planner" if state.get("is_relevant", True) else "refusal"

def route_planner(state: AgentState) -> str:
    """Route after planning check."""
    return "executor" if state.get("can_fulfill", True) else "refusal"

def build_options_agent_graph():
    """Builds and compiles the LangGraph StateGraph workflow."""
    workflow = StateGraph(AgentState)

    workflow.add_node("guardrail", guardrail_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("refusal", refusal_node)

    workflow.add_edge(START, "guardrail")
    workflow.add_conditional_edges("guardrail", route_guardrail, {"planner": "planner", "refusal": "refusal"})
    workflow.add_conditional_edges("planner", route_planner, {"executor": "executor", "refusal": "refusal"})
    workflow.add_edge("executor", "synthesizer")
    workflow.add_edge("synthesizer", END)
    workflow.add_edge("refusal", END)

    return workflow.compile()

# Pre-compiled graph
app = build_options_agent_graph()


# ==============================================================================
# Main Agent Runner & CLI
# ==============================================================================

def run_agent(query: str, verbose: bool = True) -> str:
    """Main entry point to execute the LangGraph workflow."""
    if not GEMINI_API_KEY:
        return "❌ Error: GEMINI_API_KEY is not set in environment or .env file."

    initial_state: AgentState = {
        "query": query,
        "is_relevant": True,
        "guardrail_reason": "",
        "can_fulfill": True,
        "sandbox_reason": "",
        "plan_summary": "",
        "steps": [],
        "execution_results": [],
        "final_response": "",
        "verbose": verbose
    }

    final_state = app.invoke(initial_state)
    return final_state.get("final_response", "")


def main():
    parser = argparse.ArgumentParser(description="Options Tracker LangGraph Agent (Sandbox Mode)")
    parser.add_argument("query", nargs="*", default=[], help="The query to ask the agent.")
    parser.add_argument("--query", "-q", dest="query_opt", default=None, help="The query to ask the agent.")
    parser.add_argument("--quiet", action="store_true", help="Suppress intermediate step logs.")
    args = parser.parse_args()

    if args.query_opt:
        user_query = args.query_opt
    elif args.query:
        user_query = " ".join(args.query)
    else:
        user_query = None

    if user_query:
        output = run_agent(user_query, verbose=not args.quiet)
        print(output)
    else:
        print("\n========================================================")
        print("🤖 Options Tracker LangGraph Agent (Sandbox Mode)")
        print("========================================================")
        print("Type your query or 'exit' / 'quit' to end.\n")

        while True:
            try:
                query = input("\nOptions Agent > ").strip()
                if not query:
                    continue
                if query.lower() in ["exit", "quit", "q"]:
                    print("Goodbye!")
                    break
                output = run_agent(query, verbose=not args.quiet)
                print(output)
            except KeyboardInterrupt:
                print("\nExiting...")
                break
            except Exception as e:
                print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
