"""Session 13 — the estimation flow as an explicit LangGraph ``StateGraph``.

Where Session 12 drove the flow with a hand-written reason→act→observe loop, this
package re-expresses it as a graph: five typed nodes wired sequentially, a shared
``TypedDict`` state with accumulator reducers, a Postgres checkpointer for
persistence, and Logfire spans for observability.

Architecturally the graph is CONDUCTOR territory (it composes ``generation/rag``
retrieval + ``foundation/llm`` generation), so it lives under ``app/domain/``
beside ``estimation_service.py`` — the only layer allowed to compose generation
siblings (``ARCHITECTURE.md`` §7). Nodes self-wire their dependencies through
local ``from app.dependencies import ...`` imports (the tolerated composition-root
touch already used by ``app/generation/rag/agent_retrieval.py``), so each node
stays a pure ``state -> partial update`` function.

The external contract is unchanged: transcript in, structured estimate + ``status``
out. The Rails business backend is oblivious to the graph underneath.
"""

from __future__ import annotations

from app.domain.graph.build import build_graph
from app.domain.graph.state import BudgetMatch, Component, EstimationState

__all__ = ["build_graph", "BudgetMatch", "Component", "EstimationState"]
