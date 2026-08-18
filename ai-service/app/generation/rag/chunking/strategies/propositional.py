"""Propositional chunker.

An LLM decomposes each component into self-contained atomic propositions; each
proposition becomes its own chunk. Precise and great for fact-level retrieval,
but expensive: one LLM call per component. We capture token usage and report the
extra cost.

Requires an OpenAI key. Uses Instructor for a typed ``list[str]`` response.
"""

from __future__ import annotations

import time

import instructor
import structlog
from openai import OpenAI
from pydantic import BaseModel, Field

from app.generation.rag.chunking.base import Chunker, count_tokens, emit_chunking_done
from app.generation.rag.chunking.structural import component_metadata, render_component_text
from app.generation.rag.schemas import Budget, Chunk

log = structlog.get_logger()

# gpt-4o-mini pricing (input/output). CHANGES OVER TIME.
GPT4O_MINI_INPUT_PER_MILLION_USD = 0.15
GPT4O_MINI_OUTPUT_PER_MILLION_USD = 0.60

_SYSTEM_PROMPT = (
    "Decompose the following content into clear, simple, self-contained "
    "propositions. Each proposition must:\n"
    "1. Express a single atomic fact.\n"
    "2. Be understandable on its own, without the surrounding text.\n"
    "3. Resolve pronouns and references to the explicit entity they refer to "
    "(e.g. replace 'it' with the component name).\n"
    "Return the list of propositions."
)


class _Propositions(BaseModel):
    propositions: list[str] = Field(default_factory=list)


class PropositionalChunker(Chunker):
    strategy_name = "propositional"

    def __init__(self, client: OpenAI, model: str = "gpt-4o-mini") -> None:
        self._client = instructor.from_openai(client)
        self._model = model

    def chunk(self, budgets: list[Budget]) -> list[Chunk]:
        t0 = time.perf_counter()
        chunks: list[Chunk] = []
        calls = 0
        cost = 0.0
        for budget in budgets:
            for component in budget.components:
                text = render_component_text(budget, component)
                props, usage = self._decompose(text)
                calls += 1
                cost += usage
                if not props:
                    # Fallback: keep the component as a single chunk so a failed
                    # decomposition never drops content silently.
                    props = [text]
                for j, prop in enumerate(props):
                    chunks.append(
                        Chunk(
                            chunk_id=f"{budget.budget_id}::{component.component_id}::prop{j}",
                            text=prop,
                            metadata={**component_metadata(budget, component), "proposition": j},
                            token_count=count_tokens(prop),
                        )
                    )
        self.last_extra_api_calls = calls
        self.last_extra_cost_usd = cost
        emit_chunking_done(
            strategy=self.strategy_name,
            chunks=chunks,
            n_input_documents=len(budgets),
            extra_api_calls=calls,
            extra_cost_usd=cost,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        return chunks

    def _decompose(self, text: str) -> tuple[list[str], float]:
        """Return (propositions, cost_usd). Logs and degrades on any API error."""
        try:
            result, completion = self._client.chat.completions.create_with_completion(
                model=self._model,
                response_model=_Propositions,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "propositional_call_failed", error_type=type(exc).__name__, error=str(exc)[:200]
            )
            return [], 0.0
        usage = completion.usage
        cost = (
            usage.prompt_tokens / 1_000_000 * GPT4O_MINI_INPUT_PER_MILLION_USD
            + usage.completion_tokens / 1_000_000 * GPT4O_MINI_OUTPUT_PER_MILLION_USD
        )
        return result.propositions, cost
