"""Chunking strategies for the Session 7 comparison.

Seven strategies behind the common :class:`~app.generation.rag.chunking.base.Chunker`
interface (the structural chunker lives in ``app.generation.rag.chunking.structural``).
Re-exported here so ``from app.generation.rag.chunking.strategies import *`` is a
one-line pre-flight check that every strategy imports cleanly.
"""

from app.generation.rag.chunking.strategies.contextual_retrieval import ContextualRetrievalChunker
from app.generation.rag.chunking.strategies.fixed_size import FixedSizeChunker
from app.generation.rag.chunking.strategies.hierarchical import HierarchicalChunker
from app.generation.rag.chunking.strategies.propositional import PropositionalChunker
from app.generation.rag.chunking.strategies.recursive import RecursiveChunker
from app.generation.rag.chunking.strategies.semantic import SemanticChunker
from app.generation.rag.chunking.strategies.sentence_window import SentenceWindowChunker

__all__ = [
    "FixedSizeChunker",
    "RecursiveChunker",
    "SentenceWindowChunker",
    "SemanticChunker",
    "PropositionalChunker",
    "ContextualRetrievalChunker",
    "HierarchicalChunker",
]
