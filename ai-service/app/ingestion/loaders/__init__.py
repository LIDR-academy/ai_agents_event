"""Loaders — raw byte producers.

A loader knows how to enumerate files (or URLs, or DB rows) and hand the
parser a stream of ``(name, bytes)`` pairs. Loaders never look INTO the bytes
— that's the parser's job. The split keeps both halves small enough to test.
"""

from app.ingestion.loaders.filesystem import FileSystemLoader, LoadedBlob

__all__ = ["FileSystemLoader", "LoadedBlob"]
