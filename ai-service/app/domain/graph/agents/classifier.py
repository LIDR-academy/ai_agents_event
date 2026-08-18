"""``classifier_agent`` — the flow's entry agent (complexity + reformulation).

Reads the raw meeting transcript and does two jobs in one structured call: it judges
how COMPLEX the estimation will be (mapped later to the structure agent's reasoning
effort) and REFORMULATES the transcript into a clean, self-contained project brief.

It then performs an explicit HANDOVER to ``structure_agent`` via ``Command(goto=...,
update=...)`` — passing both control and the state it produced. That is the first of
the two agent-to-agent handovers the live session highlights.
"""

from __future__ import annotations

import asyncio

import logfire
import structlog
from langgraph.types import Command

from app.config import get_settings
from app.domain.graph.personas import persona_for
from app.domain.graph.schemas import ComplexityClassification

log = structlog.get_logger()

_CLASSIFIER_SYSTEM_PROMPT = (
    "You are an estimation triage analyst. You are given a raw, messy client meeting "
    "transcript (any language). Do TWO things:\n"
    "1. Judge the COMPLEXITY of the estimation this project will require: 'low' (a "
    "single simple component), 'medium' (a few related components) or 'high' (many "
    "dispares components and/or third-party integrations).\n"
    "2. REFORMULATE the transcript into a clean, self-contained project brief in "
    "concise technical English: the components the client wants, their scope and "
    "constraints, with small talk, anecdotes and digressions removed. Never invent "
    "scope the transcript gives no evidence for.\n"
    "Return the complexity, the reformulated brief and one line on why."
)


async def classifier_agent(state: dict) -> Command:
    """Transcript → (complexity, reformulated brief) → handover to structure_agent."""
    with logfire.span("node: classifier_agent"):
        settings = get_settings()
        from app.dependencies import get_llm_wrapper

        wrapper = get_llm_wrapper()
        persona = persona_for("classifier_agent", enabled=settings.GRAPH_PERSONAS_ENABLED)
        system_prompt = f"{persona}\n\n{_CLASSIFIER_SYSTEM_PROMPT}" if persona else _CLASSIFIER_SYSTEM_PROMPT
        result, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=system_prompt,
            user_message=state["transcript"],
            response_model=ComplexityClassification,
            model_override=settings.GRAPH_CLASSIFIER_MODEL,
        )
        log.info(
            "agent_classifier_done",
            complexity=result.complexity,
            brief_chars=len(result.reformulated_transcript),
        )
        # Explicit handover: pass control AND the produced state to structure_agent.
        return Command(
            goto="structure_agent",
            update={
                "complexity": result.complexity,
                "reformulated_transcript": result.reformulated_transcript,
            },
        )
