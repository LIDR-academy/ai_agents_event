"""Retrieval-augmented generation.

- ``chunking/`` — the ``Chunker`` interface, the structural chunker and the
  comparison strategies (Session 7).
- ``embedding/`` — the OpenAI embedder.
- ``analysis/`` — similarity + strategy-comparison tooling.
- ``store/`` — vector persistence (pgvector). Reserved for Session 8.
- ``retriever.py`` — semantic retrieval over the store. Reserved for Session 8.
- ``retrieval/`` — advanced retrieval: hybrid search, reranking, multi-index
  routing, query transform, temporal decay (Sessions 8–10).
- ``quality/`` — generation quality: augmentation, the hallucination gate and
  contradictory-source synthesis (Session 11).

Today vectors are produced in memory and returned over HTTP; persistence and
retrieval land in Session 8.
"""
