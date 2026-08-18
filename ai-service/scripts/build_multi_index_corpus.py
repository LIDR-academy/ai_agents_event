#!/usr/bin/env python3
"""Ingest the Session 10 transcript + technical-doc collections (multi-index).

The budgets corpus is ingested over HTTP by ``query_examples.py``; this script
seeds the two NEW collections (``transcript_chunks``, ``technical_doc_chunks``)
directly through the project's ``ChunkStore``, reusing the same embedder and async
session factory. One chunk per transcript segment / per doc section, each with the
collection's own metadata schema (transcripts: speakers + meeting_date; docs:
version) so the routing, hard-filter and temporal-decay demos return sensible
results.

Idempotent: each document is keyed by ``source_path`` and skipped if already
ingested, so re-running never duplicates data. Wipe with::

    DELETE FROM documents WHERE document_type IN ('meeting_transcript','technical_doc');

Run it INSIDE the container (host macOS/arm64 hits the async-greenlet issue with
direct pgvector access)::

    docker compose exec ai-service python scripts/build_multi_index_corpus.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.s08_common import require_embedder  # noqa: E402

from app.dependencies import get_chunk_store, get_token_encoder  # noqa: E402
from app.foundation.persistence.database import get_async_session_factory  # noqa: E402
from app.generation.rag.retrieval.collections import Collection, spec_for  # noqa: E402
from app.generation.rag.schemas import Chunk  # noqa: E402

TRANSCRIPTS_PATH = ROOT / "data" / "transcripts_sample.json"
DOCS_PATH = ROOT / "data" / "technical_docs_sample.json"


def _chunk(encoder, *, chunk_id: str, header: str, body: str, metadata: dict) -> Chunk:
    """Build one embeddable chunk: a small contextual header + the body text."""
    text = f"{header}\n{body}".strip()
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        metadata=metadata,
        token_count=len(encoder.encode(text)),
    )


def _transcript_chunks(encoder, transcript: dict) -> list[Chunk]:
    tid = transcript["transcript_id"]
    header = f"Meeting: {transcript['project']} ({transcript['meeting_date']})"
    chunks = []
    for segment in transcript["segments"]:
        chunks.append(
            _chunk(
                encoder,
                chunk_id=f"{tid}::{segment['segment_id']}",
                header=f"{header} — topic: {segment['topic']}",
                body=segment["text"],
                metadata={
                    "transcript_id": tid,
                    "segment_id": segment["segment_id"],
                    "project": transcript["project"],
                    "meeting_date": transcript["meeting_date"],
                    "speakers": segment.get("speakers", []),
                    "topic": segment["topic"],
                },
            )
        )
    return chunks


def _doc_chunks(encoder, doc: dict) -> list[Chunk]:
    did = doc["doc_id"]
    header = f"Doc: {doc['title']} (version {doc['version']})"
    chunks = []
    for section in doc["sections"]:
        chunks.append(
            _chunk(
                encoder,
                chunk_id=f"{did}::{section['section_id']}",
                header=f"{header} — {section['heading']}",
                body=section["text"],
                metadata={
                    "doc_id": did,
                    "section_id": section["section_id"],
                    "heading": section["heading"],
                    "version": doc["version"],
                    "component": doc.get("component", "unknown"),
                },
            )
        )
    return chunks


async def _ingest_documents(*, label, items, source_file, document_type, collection, build_chunks):
    """Embed + persist each document of one collection, skipping duplicates."""
    embedder = require_embedder()
    encoder = get_token_encoder()
    store = get_chunk_store()
    session_factory = get_async_session_factory()
    spec = spec_for(collection)

    created, skipped = 0, 0
    for item in items:
        doc_key = item.get("transcript_id") or item.get("doc_id")
        source_path = f"{source_file}::{doc_key}"

        async with session_factory() as session:
            if await store.find_document_id(session, source_path) is not None:
                skipped += 1
                continue

        chunks = build_chunks(encoder, item)
        embedded = embedder.embed_many(chunks)

        async with session_factory() as session, session.begin():
            await store.persist_document_with_chunks(
                session,
                source_path=source_path,
                document_type=document_type,
                doc_metadata={"source_id": doc_key},
                embedded_chunks=embedded,
                chunk_type=spec.chunk_type,
                model=spec.model,
            )
        created += 1

    print(f"{label}: {len(items)} documents — {created} ingested, {skipped} already present.")


async def main() -> int:
    transcripts = json.loads(TRANSCRIPTS_PATH.read_text(encoding="utf-8"))
    docs = json.loads(DOCS_PATH.read_text(encoding="utf-8"))

    await _ingest_documents(
        label="Transcripts",
        items=transcripts,
        source_file="data/transcripts_sample.json",
        document_type="meeting_transcript",
        collection=Collection.TRANSCRIPT,
        build_chunks=_transcript_chunks,
    )
    await _ingest_documents(
        label="Technical docs",
        items=docs,
        source_file="data/technical_docs_sample.json",
        document_type="technical_doc",
        collection=Collection.TECHNICAL_DOC,
        build_chunks=_doc_chunks,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
