"""Filesystem loader.

Resolves the ``CatalogSource.location`` against the project's
``INGESTION_DATA_ROOT``, walks the resulting path, and yields one
``LoadedBlob`` per file whose extension matches the source's declared format.
A non-existent location is a hard error: a catalog that points nowhere is
silently wrong, the worst kind of broken.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoadedBlob:
    """A single file's bytes plus the relative path within the data root."""

    relative_path: str
    bytes_: bytes


class FileSystemLoader:
    def __init__(self, data_root: Path | str) -> None:
        self._data_root = Path(data_root)

    def iter_blobs(self, location: str, formats: set[str]) -> Iterator[LoadedBlob]:
        path = self._data_root / location
        if not path.exists():
            raise FileNotFoundError(
                f"Catalog location {location!r} does not resolve to a real path "
                f"({path!s} not found)"
            )
        if path.is_file():
            candidates = [path]
        else:
            candidates = sorted(p for p in path.rglob("*") if p.is_file())
        for candidate in candidates:
            ext = candidate.suffix.lower().lstrip(".")
            if ext not in formats:
                continue
            yield LoadedBlob(
                relative_path=str(candidate.relative_to(self._data_root)),
                bytes_=candidate.read_bytes(),
            )
