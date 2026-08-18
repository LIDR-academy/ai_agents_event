"""Generation layer — the three composable AI architectures.

- ``cag/`` — cache-augmented generation (exact + semantic caches).
- ``rag/`` — retrieval-augmented generation (chunking, embedding, store, retriever).
- ``agentic/`` — Actor-Critic-Boss orchestration.
- ``conversation/`` — the multi-turn substrate the agentic loop builds on.

Architectural rule: these sub-packages MUST NOT import one another. They
compose only through :class:`app.domain.estimation_service.EstimationService`.
The one documented exception is ``agentic`` → ``conversation`` (one-way).
"""
