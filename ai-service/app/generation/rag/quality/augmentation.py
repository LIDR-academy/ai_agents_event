"""Session 11 — context augmentation: shape the retrieved chunks before assembly.

Two deterministic passes applied to the token-truncated chunks BEFORE they become
the ``<source>`` block (:mod:`app.generation.rag.context_assembler`), each
independently switchable so it can be measured in isolation:

* :func:`extract_key_points` / :func:`compress_chunk` — extractive compression:
  keep the lines that carry a figure or an id (the component name + hours the
  generator must ground on), drop the filler, so the token budget buys more
  distinct sources.
* :func:`reorder_edge_loaded` — a countermeasure to *lost-in-the-middle*: put the
  strongest sources at both ends of the context and bury the weakest in the middle.

Pure functions, no LLM, no cost. Ids are preserved, so downstream citation
verification is unaffected.
"""

from __future__ import annotations

from app.generation.rag.schemas import RetrievedChunk


def extract_key_points(chunk: RetrievedChunk) -> str:
    """Extractive compression: keep only the lines that carry a figure or an id.

    A retrieved budget chunk is mostly prose the generator does not need; what it
    must ground on is the component name, its id and its hours. This deterministic
    (free, no LLM) pass keeps lines that carry a digit or a code-like token and
    drops the filler, so the token budget buys more distinct sources.
    """
    kept = [
        line.strip()
        for line in chunk.content.splitlines()
        if line.strip() and (any(ch.isdigit() for ch in line) or "::" in line)
    ]
    # Never compress to nothing: fall back to the first non-empty line.
    if not kept:
        kept = [next((ln.strip() for ln in chunk.content.splitlines() if ln.strip()), "")]
    return "\n".join(kept)


def compress_chunk(chunk: RetrievedChunk) -> RetrievedChunk:
    """Return a copy of ``chunk`` whose content is its extracted key points."""
    return chunk.model_copy(update={"content": extract_key_points(chunk)})


def reorder_edge_loaded(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Reorder relevance-sorted chunks so the strongest sit at BOTH ends.

    Countermeasure to *lost-in-the-middle*: models attend most to the start and
    end of a long context. Given chunks sorted best-first, this places even ranks
    at the head (ascending) and odd ranks at the tail (so the 2nd-best lands last),
    burying the weakest sources in the middle. Pure function; ids are untouched.
    """
    head: list[RetrievedChunk] = []
    tail: list[RetrievedChunk] = []
    for i, chunk in enumerate(chunks):
        (head if i % 2 == 0 else tail).append(chunk)
    return head + tail[::-1]


def augment_chunks(
    chunks: list[RetrievedChunk], *, compress: bool = True, reorder: bool = True
) -> list[RetrievedChunk]:
    """Apply the Session 11 augmentation passes (compress → edge-load reorder).

    Each pass is independently switchable so it can be measured in isolation.
    Order matters: compress first (cheaper tokens), then reorder for position.
    """
    out = [compress_chunk(c) for c in chunks] if compress else list(chunks)
    if reorder:
        out = reorder_edge_loaded(out)
    return out
