"""Semantic chunker.

Embeds consecutive sentences and cuts where the embedding similarity drops
(a "semantic breakpoint"). The catch is cost: it has to embed during *ingestion*
— roughly one extra embedding pass over the whole corpus — before a single
query is ever served. We surface that extra cost explicitly.

Wraps ``langchain_experimental``'s SemanticChunker. Requires an OpenAI key.
"""

from __future__ import annotations

import time

from langchain_experimental.text_splitter import SemanticChunker as _LCSemanticChunker
from langchain_openai import OpenAIEmbeddings

from app.generation.rag.chunking.base import Chunker, count_tokens, emit_chunking_done
from app.generation.rag.chunking.structural import serialize_budget
from app.generation.rag.schemas import Budget, Chunk

# Breakpoint policy. "percentile" cuts when the inter-sentence distance is above
# the Nth percentile of all distances in the document. 95 = only the sharpest
# topic shifts become boundaries.
BREAKPOINT_THRESHOLD_TYPE = "percentile"
BREAKPOINT_THRESHOLD_AMOUNT = 95

# Embedding price (input). CHANGES OVER TIME — text-embedding-3-small.
EMBEDDING_PRICE_PER_MILLION_USD = 0.02


class SemanticChunker(Chunker):
    strategy_name = "semantic"

    def __init__(self, api_key: str | None, model: str = "text-embedding-3-small") -> None:
        if not api_key:
            raise RuntimeError("SemanticChunker requires OPENAI_API_KEY.")
        self._embeddings = OpenAIEmbeddings(model=model, api_key=api_key)
        self._splitter = _LCSemanticChunker(
            self._embeddings,
            breakpoint_threshold_type=BREAKPOINT_THRESHOLD_TYPE,
            breakpoint_threshold_amount=BREAKPOINT_THRESHOLD_AMOUNT,
        )

    def chunk(self, budgets: list[Budget]) -> list[Chunk]:
        t0 = time.perf_counter()
        chunks: list[Chunk] = []
        extra_api_calls = 0
        extra_tokens = 0
        for budget in budgets:
            text = serialize_budget(budget)
            pieces = self._splitter.split_text(text)
            # SemanticChunker embeds every sentence of the document once, batched
            # into a single embed_documents call per document.
            extra_api_calls += 1
            extra_tokens += count_tokens(text)
            for i, piece in enumerate(pieces):
                chunks.append(
                    Chunk(
                        chunk_id=f"{budget.budget_id}::sem{i}",
                        text=piece,
                        metadata={
                            "budget_id": budget.budget_id,
                            "client_sector": budget.client_metadata.sector,
                            "main_technology": budget.main_technology,
                            "year": budget.year,
                            "part": i,
                        },
                        token_count=count_tokens(piece),
                    )
                )
        self.last_extra_api_calls = extra_api_calls
        self.last_extra_cost_usd = extra_tokens / 1_000_000 * EMBEDDING_PRICE_PER_MILLION_USD
        emit_chunking_done(
            strategy=self.strategy_name,
            chunks=chunks,
            n_input_documents=len(budgets),
            extra_api_calls=extra_api_calls,
            extra_cost_usd=self.last_extra_cost_usd,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        return chunks
