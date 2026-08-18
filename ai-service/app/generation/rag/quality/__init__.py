"""Session 11 — generation quality: grounding guards over the RAG estimate.

Three techniques layered on top of the Session 9 grounded generation, each
switchable at runtime (``RuntimeRetrievalConfig`` → Ajustes UI):

* ``augmentation`` — shape the retrieved context BEFORE generation: compress each
  source to its key points and edge-load reorder against lost-in-the-middle.
* ``hallucination`` — the semantic gate AFTER generation: a deterministic numeric
  anchor + a strict judge grade each grounded line grounded / degraded /
  insufficient (referential integrity is not entailment).
* ``synthesis`` — surface contradictory historical sources as an hour range with a
  reason instead of averaging the conflict into a misleading point.

This package depends only on ``foundation`` + ``domain/schemas`` + sibling modules
under ``generation/rag`` — never on another ``generation`` family. It composes into
the request path only through the conductor (``rag/estimator.py``) and the per-task
hours pipeline (``rag/task_hours.py``), never via a cross-sibling import.
"""
