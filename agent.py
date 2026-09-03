# agent.py — DEPRECATED shim, use analyze_agent.py
"""
Backward compatibility shim.

This file re-exports everything from analyze_agent.py so existing imports
like `from agent import app` or `from agent import run_agent` continue to work.
New code should import from `analyze_agent` directly.

Will be removed in a future version.
"""
import warnings

warnings.warn(
    "Importing from `agent` is deprecated — use `analyze_agent` instead.",
    DeprecationWarning,
    stacklevel=2,
)

from analyze_agent import *  # noqa: F401,F403
from analyze_agent import (
    app,
    AgentState,
    AnalyzeAgentState,
    build_options_agent_graph,
    build_options_analyze_agent_graph,
    run_agent,
    run_analyze_agent,
    analyze_agent_app,
    call_gemini_with_retry,
    guardrail_node,
    planner_node,
    executor_node,
    synthesizer_node,
    refusal_node,
    route_guardrail,
    route_planner,
    get_tools_prompt,
)
