"""Contextual Retrieval chunker (Anthropic's technique).

Each structural chunk is enriched with a short LLM-generated paragraph that
situates it inside its parent budget, prepended to the chunk text. The parent
document is sent with ``cache_control`` so processing all the chunks of one
budget pays for the (large) document prefix once and reads it from cache
thereafter. Anthropic reports up to -67% retrieval failures when combined with
contextual BM25 + reranking.

This is the most expensive strategy to instrument and the most pedagogical.
Requires an Anthropic key.
"""

from __future__ import annotations

import time

import anthropic
import structlog

from app.generation.rag.chunking.base import Chunker, count_tokens, emit_chunking_done
from app.generation.rag.chunking.structural import (
    component_metadata,
    render_component_text,
    serialize_budget,
)
from app.generation.rag.schemas import Budget, Chunk

log = structlog.get_logger()

# claude-sonnet-4-5 pricing per million tokens. CHANGES OVER TIME.
SONNET_INPUT_PER_MILLION_USD = 3.00
SONNET_OUTPUT_PER_MILLION_USD = 15.00
SONNET_CACHE_WRITE_PER_MILLION_USD = 3.75
SONNET_CACHE_READ_PER_MILLION_USD = 0.30

MAX_CONTEXT_TOKENS = 200

# Canonical Anthropic Contextual Retrieval prompt.
_CHUNK_CONTEXT_PROMPT = (
    "Here is the chunk we want to situate within the whole document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Please give a short succinct context to situate this chunk within the "
    "overall document for the purposes of improving search retrieval of the "
    "chunk. Answer only with the succinct context and nothing else."
)


class ContextualRetrievalChunker(Chunker):
    strategy_name = "contextual_retrieval"

    def __init__(self, client: anthropic.Anthropic, model: str = "claude-sonnet-4-5") -> None:
        self._client = client
        self._model = model

    def chunk(self, budgets: list[Budget]) -> list[Chunk]:
        t0 = time.perf_counter()
        chunks: list[Chunk] = []
        calls = 0
        cost = 0.0
        for budget in budgets:
            parent_doc = serialize_budget(budget)
            for component in budget.components:
                base_text = render_component_text(budget, component)
                context, call_cost = self._situate(parent_doc, base_text)
                calls += 1
                cost += call_cost
                enriched = f"{context}\n\n{base_text}" if context else base_text
                chunks.append(
                    Chunk(
                        chunk_id=f"{budget.budget_id}::{component.component_id}",
                        text=enriched,
                        metadata={
                            **component_metadata(budget, component),
                            "generated_context": context,
                        },
                        token_count=count_tokens(enriched),
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

    def _situate(self, parent_doc: str, chunk_text: str) -> tuple[str, float]:
        """Generate the situating context for one chunk. Returns (context, cost)."""
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_CONTEXT_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": f"<document>\n{parent_doc}\n</document>",
                        # Cache the (large, stable) parent document so the other
                        # chunks of the same budget read it instead of re-paying.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": _CHUNK_CONTEXT_PROMPT.format(chunk=chunk_text),
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "contextual_call_failed", error_type=type(exc).__name__, error=str(exc)[:200]
            )
            return "", 0.0

        context = response.content[0].text.strip() if response.content else ""
        usage = response.usage
        cost = (
            getattr(usage, "input_tokens", 0) / 1_000_000 * SONNET_INPUT_PER_MILLION_USD
            + getattr(usage, "output_tokens", 0) / 1_000_000 * SONNET_OUTPUT_PER_MILLION_USD
            + getattr(usage, "cache_creation_input_tokens", 0)
            / 1_000_000
            * SONNET_CACHE_WRITE_PER_MILLION_USD
            + getattr(usage, "cache_read_input_tokens", 0)
            / 1_000_000
            * SONNET_CACHE_READ_PER_MILLION_USD
        )
        # The generated context must be legible in the logs (pre-flight check).
        log.info("contextual_chunk_context", context=context)
        return context, cost
