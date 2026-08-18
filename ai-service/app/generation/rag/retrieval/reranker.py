"""Cross-encoder reranker wrapper (Session 10).

Bi-encoders (the embedding retriever) encode the query and each document
SEPARATELY, so "relevance" is just the cosine of two vectors computed without
either side ever seeing the other — fast, but it misses fine-grained relevance.
A cross-encoder feeds the ``(query, document)`` pair through the model TOGETHER
and outputs a single relevance score, attending across both texts. Far more
accurate, far too slow to run over the whole corpus — hence recall-then-rerank:
the bi-encoder/lexical branches recall a wide candidate set cheaply, the
cross-encoder rescores just those.

This module is the wrapper (model loading + pair scoring). It does NOT decide the
recall width or the final cut — that orchestration lives in ``pipeline.retrieve``.

The model is multilingual (``cross-encoder/mmarco-mMiniLMv2-L12-H384-v1``) so it
copes with the corpus regardless of the Spanish/English question, and it is small
enough to run on CPU at teaching latencies. It loads LAZILY on first use: importing
this module must stay cheap (tests, app startup) and must not require torch weights
on disk until something actually reranks.
"""

from __future__ import annotations

import threading
import time

import structlog

from app.config import get_settings

log = structlog.get_logger()


class CrossEncoderReranker:
    """Lazily-loaded cross-encoder that rescores ``(query, document)`` pairs.

    One instance owns one loaded model. Loading is guarded by a lock so the
    first concurrent rerank does not trigger two parallel downloads/loads.
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model = None  # sentence_transformers.CrossEncoder, loaded on demand
        self._load_lock = threading.Lock()

    @classmethod
    def from_settings(cls) -> "CrossEncoderReranker":
        return cls(get_settings().RERANKER_MODEL)

    @property
    def model_name(self) -> str:
        return self._model_name

    def load(self) -> None:
        """Force-load the model now (used by the verify script and warmup)."""
        self._ensure_loaded()

    def _ensure_loaded(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:  # another thread won the race
                return self._model
            # Imported here, not at module top: keeps the import graph (and app
            # startup / unit tests) free of torch until a rerank truly happens.
            from sentence_transformers import CrossEncoder

            started = time.perf_counter()
            self._model = CrossEncoder(self._model_name)
            log.info(
                "reranker_loaded",
                model=self._model_name,
                load_ms=int((time.perf_counter() - started) * 1000),
            )
            return self._model

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Relevance score for ``query`` against each document (higher = better).

        One forward pass over all ``(query, document)`` pairs (sentence-transformers
        batches internally). Returns a score per document, in input order.
        """
        if not documents:
            return []
        model = self._ensure_loaded()
        pairs = [(query, document) for document in documents]
        started = time.perf_counter()
        scores = model.predict(pairs)
        log.info(
            "reranker_scored",
            model=self._model_name,
            pairs=len(pairs),
            score_ms=int((time.perf_counter() - started) * 1000),
        )
        return [float(score) for score in scores]

    def rerank_with_scores(
        self,
        query: str,
        candidates: list,
        *,
        top_n: int,
        text_of=lambda candidate: candidate.content,
    ) -> list[tuple]:
        """Like :meth:`rerank` but return ``(candidate, score)`` pairs.

        The score is the raw cross-encoder relevance, exposed so a later stage
        (e.g. temporal decay) can re-weight finalists by a real relevance number
        rather than by position alone. Order is stable for equal scores.
        """
        if not candidates:
            return []
        scores = self.score(query, [text_of(candidate) for candidate in candidates])
        ranked = sorted(
            zip(candidates, scores),
            key=lambda pair: pair[1],
            reverse=True,
        )
        return [(candidate, float(score)) for candidate, score in ranked[:top_n]]

    def rerank(
        self,
        query: str,
        candidates: list,
        *,
        top_n: int,
        text_of=lambda candidate: candidate.content,
    ) -> list:
        """Reorder ``candidates`` by cross-encoder score, keep the top ``top_n``.

        ``text_of`` extracts the document text from a candidate (default: a
        ``.content`` attribute) so this works for ORM rows, schema objects or
        plain dicts without coupling the reranker to any one shape. Order is
        stable for equal scores (Python's sort is stable and the candidates keep
        their recall order).
        """
        return [
            candidate
            for candidate, _score in self.rerank_with_scores(
                query, candidates, top_n=top_n, text_of=text_of
            )
        ]
