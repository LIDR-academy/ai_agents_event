"""Unit tests for the context assembler (Session 9)."""

from __future__ import annotations

from app.generation.rag.context_assembler import build_context_block, truncate_to_token_budget
from app.generation.rag.schemas import RetrievedChunk


class CharEncoder:
    """Tiny stand-in for a tiktoken encoder: one token per character."""

    def encode(self, text: str) -> list[str]:
        return list(text)


def _chunk(chunk_id: int, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=chunk_id,
        content=content,
        sector="ecommerce",
        project_year=2024,
        chunk_type="budget_component",
        distance=0.123,
    )


def test_build_context_block_wraps_each_chunk_in_source_xml():
    chunks = [_chunk(142, "Cart and checkout service")]
    block = build_context_block(chunks)

    assert '<source id="142"' in block
    assert 'sector="ecommerce"' in block
    assert 'project_year="2024"' in block
    assert 'chunk_type="budget_component"' in block
    assert 'distance="0.1230"' in block
    assert "Cart and checkout service" in block
    assert block.strip().endswith("</source>")


def test_build_context_block_preserves_order():
    block = build_context_block([_chunk(1, "first"), _chunk(2, "second")])
    assert block.index('id="1"') < block.index('id="2"')


def test_build_context_block_empty_is_empty_string():
    assert build_context_block([]) == ""


def test_truncate_keeps_whole_chunks_within_budget():
    chunks = [_chunk(1, "a" * 50), _chunk(2, "b" * 50), _chunk(3, "c" * 50)]
    encoder = CharEncoder()

    # Budget large enough for exactly the first two wrapped chunks.
    first_two_cost = sum(
        len(encoder.encode(part))
        for part in (build_context_block([chunks[0]]), build_context_block([chunks[1]]))
    )
    kept = truncate_to_token_budget(chunks, first_two_cost, encoder)

    assert [c.id for c in kept] == [1, 2]
    # Every kept chunk is whole — content is never sliced.
    assert all(set(c.content) == {chr(ord("a") + c.id - 1)} for c in kept)


def test_truncate_zero_budget_keeps_nothing():
    chunks = [_chunk(1, "anything")]
    assert truncate_to_token_budget(chunks, 0, CharEncoder()) == []


def test_truncate_counts_xml_wrapper_not_just_content():
    chunk = _chunk(1, "x")
    encoder = CharEncoder()
    content_only = len(encoder.encode("x"))
    wrapped = len(encoder.encode(build_context_block([chunk])))

    # A budget that fits the raw content but NOT the wrapped element keeps nothing.
    assert content_only < wrapped
    assert truncate_to_token_budget([chunk], content_only, encoder) == []
