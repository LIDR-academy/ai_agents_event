"""Fixed-size chunker — the degenerate baseline.

Serializes each component to text and slides a fixed token window over it with
overlap. It ignores every natural boundary (sentence, component, JSON
structure), so windows routinely cut through the middle of a word. Useful only
as the floor to compare every smarter strategy against.
"""

from __future__ import annotations

import time

import tiktoken

from app.generation.rag.chunking.base import Chunker, emit_chunking_done
from app.generation.rag.chunking.structural import component_metadata, render_component_text
from app.generation.rag.schemas import Budget, Chunk

# Window geometry, in tokens. Clearly labeled module constants — the only knobs.
CHUNK_SIZE_TOKENS = 512
OVERLAP_TOKENS = 80

_ENCODING = tiktoken.get_encoding("cl100k_base")


class FixedSizeChunker(Chunker):
    strategy_name = "fixed_size"

    def chunk(self, budgets: list[Budget]) -> list[Chunk]:
        t0 = time.perf_counter()
        step = CHUNK_SIZE_TOKENS - OVERLAP_TOKENS
        chunks: list[Chunk] = []
        for budget in budgets:
            for component in budget.components:
                text = render_component_text(budget, component)
                tokens = _ENCODING.encode(text)
                part = 0
                start = 0
                while start < len(tokens):
                    window = tokens[start : start + CHUNK_SIZE_TOKENS]
                    piece = _ENCODING.decode(window)
                    chunks.append(
                        Chunk(
                            chunk_id=f"{budget.budget_id}::{component.component_id}::p{part}",
                            text=piece,
                            metadata={**component_metadata(budget, component), "part": part},
                            token_count=len(window),
                        )
                    )
                    part += 1
                    if start + CHUNK_SIZE_TOKENS >= len(tokens):
                        break
                    start += step
        self.last_extra_api_calls = 0
        self.last_extra_cost_usd = 0.0
        emit_chunking_done(
            strategy=self.strategy_name,
            chunks=chunks,
            n_input_documents=len(budgets),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        return chunks
