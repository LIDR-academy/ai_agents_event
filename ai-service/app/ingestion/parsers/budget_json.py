"""Parser for the Proyecto 2 budget JSON schema.

The key design choice: we render each budget to **structured markdown**, not
``json.dumps``. The downstream retriever embeds prose, not key-value soup;
markdown gives the embedder semantic anchors (## Cliente, ## Total, ## Fases)
that a raw JSON dump lacks. The numbers stay; only the framing changes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import ClassVar

from app.ingestion.documents.models import Document, DocumentMetadata
from app.ingestion.loaders.filesystem import LoadedBlob
from app.ingestion.parsers.protocol import ParseContext


class BudgetJsonParser:
    supported_formats: ClassVar[set[str]] = {"json"}

    def parse(self, blob: LoadedBlob, context: ParseContext) -> Iterable[Document]:
        payload = json.loads(blob.bytes_)
        text = _render_budget_markdown(payload)
        doc_id = f"{context.source.name}:{payload['budget_id']}:{blob.relative_path}"
        yield Document(
            id=doc_id,
            text=text,
            metadata=DocumentMetadata(
                source_name=context.source.name,
                source_version=context.source_version,
                ingested_at=context.ingested_at,
                lineage=list(context.source.lineage),
                sensitivity_pii_flags=list(context.source.sensitivity.pii_flags),
                sensitivity_access_level=context.source.sensitivity.access_level,
                location=blob.relative_path,
                extra={
                    "budget_id": payload.get("budget_id"),
                    "client_code": payload.get("client_code"),
                    "currency": payload.get("currency"),
                    "total_amount": payload.get("total_amount"),
                    "signed_at": payload.get("signed_at"),
                },
            ),
        )


def _render_budget_markdown(payload: dict) -> str:
    """Translate the budget JSON to a markdown document.

    The structure is deliberate: a single H1 (the budget id), then one H2 per
    semantic block. This is the kind of text the embedder is best at.
    """
    lines: list[str] = []
    lines.append(f"# Presupuesto {payload.get('budget_id', '?')}")
    lines.append("")

    lines.append("## Cliente")
    lines.append(f"- Nombre: {payload.get('client_name', '-')}")
    lines.append(f"- Código interno: {payload.get('client_code', '-')}")
    if payload.get("contact"):
        lines.append(f"- Contacto: {payload['contact']}")
    if payload.get("contact_email"):
        lines.append(f"- Email: {payload['contact_email']}")
    lines.append("")

    lines.append("## Total")
    lines.append(f"- Importe: {payload.get('total_amount', '?')} {payload.get('currency', '?')}")
    lines.append(f"- Firmado: {payload.get('signed_at', '-')}")
    lines.append("")

    phases = payload.get("phases") or []
    lines.append("## Fases")
    if not phases:
        lines.append("- (sin fases declaradas)")
    else:
        for phase in phases:
            lines.append(
                f"- **{phase.get('name', '?')}** — "
                f"{phase.get('weeks', '?')} semanas · "
                f"{phase.get('amount', '?')} {payload.get('currency', '?')}"
            )
    lines.append("")

    if payload.get("notes"):
        lines.append("## Notas")
        lines.append(payload["notes"])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
