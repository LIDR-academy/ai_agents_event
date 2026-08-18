"""Augmentation: assemble retrieved chunks into a delimited context block.

Two anti-patterns this module exists to avoid:

* ``"\\n\\n".join(chunk.content for chunk in chunks)`` — the model cannot tell
  where one source ends and the next begins, nor which id to cite. Each chunk is
  wrapped in an ``<source>`` element carrying its id and metadata so the
  generator can ground every claim in a specific, citable source.
* Splitting a chunk mid-way when the context overflows. :func:`truncate_to_token_budget`
  drops whole chunks from the tail (least relevant first) — a half budget line
  item is worse than one fewer budget line item.
"""

from __future__ import annotations

from app.generation.rag.schemas import RetrievedChunk


def _wrap_chunk(chunk: RetrievedChunk) -> str:
    """Render a single chunk as a self-describing ``<source>`` XML element.

    ``id`` is what a line cites in its per-line ``SourceReference.chunk_id``;
    ``document_id`` is the parent budget the chunk belongs to (copied into
    ``SourceReference.document_id``). Both are exposed so the generator can
    attribute every line to a concrete, verifiable source.
    """
    document_id = chunk.source_id or chunk.budget_id or "unknown"
    return (
        f'<source id="{chunk.id}" document_id="{document_id}" sector="{chunk.sector}" '
        f'project_year="{chunk.project_year}" chunk_type="{chunk.chunk_type}" '
        f'distance="{chunk.distance:.4f}">\n'
        f"{chunk.content}\n"
        f"</source>"
    )


def build_context_block(chunks: list[RetrievedChunk]) -> str:
    """Build the XML context block fed to the generator.

    Chunks are emitted most-relevant-first (the retriever already returns them
    ascending by distance). Each chunk becomes one ``<source>`` element.

    Parameters
    ----------
    chunks:
        Retrieved chunks, ordered by ascending distance.

    Returns
    -------
    str
        The concatenated ``<source>`` blocks (empty string when no chunks).
    """
    return "\n".join(_wrap_chunk(chunk) for chunk in chunks)


def truncate_to_token_budget(
    chunks: list[RetrievedChunk],
    max_context_tokens: int,
    encoder,
) -> list[RetrievedChunk]:
    """Keep as many leading chunks as fit within ``max_context_tokens``.

    Tokens are counted over the chunk **already wrapped in its ``<source>`` XML**,
    not just the raw content — the delimiters are real tokens the model will see.
    Chunks are kept in order; the first chunk that would overflow the budget
    stops the loop, and no chunk is ever split.

    Parameters
    ----------
    chunks:
        Retrieved chunks, ordered by ascending distance.
    max_context_tokens:
        Hard token ceiling for the assembled context block.
    encoder:
        A ``tiktoken`` encoder (``cl100k_base``) exposing ``encode``.

    Returns
    -------
    list[RetrievedChunk]
        The leading subset that fits within the budget.
    """
    kept: list[RetrievedChunk] = []
    used = 0
    for chunk in chunks:
        cost = len(encoder.encode(_wrap_chunk(chunk)))
        if used + cost > max_context_tokens:
            break
        kept.append(chunk)
        used += cost
    return kept
