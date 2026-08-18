"""Pseudonym mapping repository.

The ``lookup_or_create`` operation is the one the pseudonymizer calls per
detected entity. It must be idempotent under contention: two parallel calls
with the same ``(entity_type, original_hash)`` MUST converge to the same
pseudonym — otherwise the corpus would acquire two different pseudonyms for
the same person and the consistency guarantee breaks.

We rely on Postgres' UNIQUE constraint to enforce this: insert first, fall
back to a SELECT when the insert conflicts. This is cheaper than a SELECT-
then-INSERT race and survives concurrent writers without explicit locking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.foundation.persistence.models import PseudonymMappingRow


@dataclass(frozen=True)
class Mapping:
    """In-memory view of a row. Repositories never leak SQLAlchemy types."""

    entity_type: str
    original_hash: str
    pseudonym: str
    created_at: datetime


class MappingsRepository:
    """Thin wrapper around the ``pseudonym_mappings`` table."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lookup(self, entity_type: str, original_hash: str) -> Mapping | None:
        row = self._session.execute(
            select(PseudonymMappingRow).where(
                PseudonymMappingRow.entity_type == entity_type,
                PseudonymMappingRow.original_hash == original_hash,
            )
        ).scalar_one_or_none()
        return _to_mapping(row) if row is not None else None

    def lookup_or_create(
        self,
        entity_type: str,
        original_hash: str,
        new_pseudonym_factory,
    ) -> Mapping:
        """Return the existing mapping or insert a new one, deterministically.

        ``new_pseudonym_factory`` is a zero-arg callable invoked only when no
        prior mapping exists. It is passed lazily because generating a Faker
        value is cheap but pointless on a cache hit.
        """
        existing = self.lookup(entity_type, original_hash)
        if existing is not None:
            return existing

        pseudonym = new_pseudonym_factory()
        stmt = (
            pg_insert(PseudonymMappingRow)
            .values(
                entity_type=entity_type,
                original_hash=original_hash,
                pseudonym=pseudonym,
            )
            .on_conflict_do_nothing(index_elements=["entity_type", "original_hash"])
            .returning(PseudonymMappingRow)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        self._session.commit()

        if row is not None:
            return _to_mapping(row)

        # Another writer won the race; read the winning row.
        winner = self.lookup(entity_type, original_hash)
        assert winner is not None, "unique constraint guaranteed a winner exists"
        return winner

    def forget(self, entity_type: str, original_hash: str) -> bool:
        """Delete a mapping. Returns True if a row existed."""
        deleted = self._session.execute(
            PseudonymMappingRow.__table__.delete().where(
                PseudonymMappingRow.entity_type == entity_type,
                PseudonymMappingRow.original_hash == original_hash,
            )
        )
        self._session.commit()
        return deleted.rowcount > 0


def _to_mapping(row: PseudonymMappingRow) -> Mapping:
    return Mapping(
        entity_type=row.entity_type,
        original_hash=row.original_hash,
        pseudonym=row.pseudonym,
        created_at=row.created_at,
    )
