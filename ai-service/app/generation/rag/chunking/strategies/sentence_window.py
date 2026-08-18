"""Sentence-window chunker.

Indexes individual sentences (small, precise units → high recall) but carries a
±N-sentence window in ``metadata.window_text``. The bare sentence is what gets
embedded; at retrieval time the window is what you would feed the generator, so
the LLM still sees surrounding context. The contrast to embed is that the
indexed unit and the returned unit are deliberately *different*.
"""

from __future__ import annotations

import time

import nltk

from app.generation.rag.chunking.base import Chunker, count_tokens, emit_chunking_done
from app.generation.rag.chunking.structural import component_metadata
from app.generation.rag.schemas import Budget, Chunk

# Sentences of context kept on each side of the indexed sentence.
WINDOW_SIZE = 2


def _ensure_punkt() -> None:
    """Make sure the NLTK sentence tokenizer data is available (lazy download)."""
    for resource in ("tokenizers/punkt_tab", "tokenizers/punkt"):
        try:
            nltk.data.find(resource)
            return
        except LookupError:
            continue
    nltk.download("punkt_tab", quiet=True)
    nltk.download("punkt", quiet=True)


class SentenceWindowChunker(Chunker):
    strategy_name = "sentence_window"

    def chunk(self, budgets: list[Budget]) -> list[Chunk]:
        t0 = time.perf_counter()
        _ensure_punkt()
        chunks: list[Chunk] = []
        for budget in budgets:
            for component in budget.components:
                sentences = nltk.sent_tokenize(component.description)
                for i, sentence in enumerate(sentences):
                    lo = max(0, i - WINDOW_SIZE)
                    hi = min(len(sentences), i + WINDOW_SIZE + 1)
                    window_text = " ".join(sentences[lo:hi])
                    chunks.append(
                        Chunk(
                            chunk_id=f"{budget.budget_id}::{component.component_id}::s{i}",
                            text=sentence,
                            metadata={
                                **component_metadata(budget, component),
                                "sentence_index": i,
                                "window_text": window_text,
                            },
                            token_count=count_tokens(sentence),
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
