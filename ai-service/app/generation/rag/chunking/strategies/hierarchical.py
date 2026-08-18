"""Hierarchical (parent-child) chunker.

Indexes two levels at once. Children = components (small, specific → good
recall). Parents = the whole serialized budget (large, full context → good for
feeding the generator). Each child carries ``metadata.parent_chunk_id``; the
S8 retriever will decide which level to return for a given query.
"""

from __future__ import annotations

import time

from app.generation.rag.chunking.base import Chunker, count_tokens, emit_chunking_done
from app.generation.rag.chunking.structural import (
    component_metadata,
    render_component_text,
    serialize_budget,
)
from app.generation.rag.schemas import Budget, Chunk


class HierarchicalChunker(Chunker):
    strategy_name = "hierarchical"

    def chunk(self, budgets: list[Budget]) -> list[Chunk]:
        t0 = time.perf_counter()
        chunks: list[Chunk] = []
        for budget in budgets:
            parent_id = f"{budget.budget_id}::parent"
            parent_text = serialize_budget(budget)
            chunks.append(
                Chunk(
                    chunk_id=parent_id,
                    text=parent_text,
                    metadata={
                        "budget_id": budget.budget_id,
                        "client_sector": budget.client_metadata.sector,
                        "main_technology": budget.main_technology,
                        "year": budget.year,
                        "level": "parent",
                    },
                    token_count=count_tokens(parent_text),
                )
            )
            for component in budget.components:
                child_text = render_component_text(budget, component)
                chunks.append(
                    Chunk(
                        chunk_id=f"{budget.budget_id}::{component.component_id}",
                        text=child_text,
                        metadata={
                            **component_metadata(budget, component),
                            "level": "child",
                            "parent_chunk_id": parent_id,
                        },
                        token_count=count_tokens(child_text),
                    )
                )
        self.last_extra_api_calls = 0
        self.last_extra_cost_usd = 0.0
        emit_chunking_done(
            strategy=self.strategy_name,
            chunks=chunks,
            n_input_documents=len(budgets),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        return chunks
