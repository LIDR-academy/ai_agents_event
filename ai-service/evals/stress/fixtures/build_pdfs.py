"""Generate calibrated synthetic PDFs for the stress runner.

Produces ``attach_{5,20,50,100}kb.pdf`` under this directory, where the
suffix refers to the *extracted text size* (what ``pypdf`` will pull out)
rather than the file size on disk — the runner cares about the prompt
budget, not the byte count.

The generator is deterministic: same paragraph, same number of repeats per
target. Re-running it yields byte-identical PDFs. The PDFs themselves are
gitignored; only this script is committed.

Run with::

    uv run python -m evals.stress.fixtures.build_pdfs
"""

from __future__ import annotations

import sys
from pathlib import Path

from fpdf import FPDF
from pypdf import PdfReader


_TARGETS_KB = (5, 20, 50, 100)

_PARAGRAPH = (
    "The supplier portal must replace the existing manual workflow. Vendors "
    "upload PDFs of compliance certificates which the procurement team reviews "
    "for completeness, expiration, and conformance to the company's standard. "
    "Approved suppliers are unlocked for purchase order generation; rejected "
    "suppliers must remediate before the next review cycle. Every state "
    "transition is recorded in an immutable audit log retained for seven years "
    "in compliance with internal policy. Reviewers may attach comments to a "
    "specific certificate; comments are visible to the supplier and form part "
    "of the formal record. The portal must integrate with the legacy ERP for "
    "vendor master data synchronisation, propagating changes within fifteen "
    "minutes. Single sign-on is mandatory for internal users; vendors use "
    "magic-link email authentication scoped to a single tenant. Performance "
    "expectations are P95 page load under two seconds with two hundred "
    "concurrent users, and the system must remain available under one hour of "
    "downtime per quarter excluding planned maintenance windows scheduled "
    "outside business hours. "
)


def _build_pdf(target_kb: int, output_path: Path) -> tuple[int, int]:
    """Build a PDF whose *extracted text* is approximately ``target_kb`` KB.

    Returns ``(text_chars, file_bytes)``. The text-vs-bytes ratio depends on
    fpdf2's PDF compression — typical ratio is ~1.5x more bytes than text.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)

    target_chars = target_kb * 1024
    text_chars = 0
    while text_chars < target_chars:
        pdf.multi_cell(0, 5, _PARAGRAPH)
        text_chars += len(_PARAGRAPH)
        if text_chars < target_chars:
            # blank line as a soft delimiter, also helps pypdf parse paragraphs
            pdf.ln(3)

    pdf.output(str(output_path))
    file_bytes = output_path.stat().st_size
    return text_chars, file_bytes


def _verify_extracted(path: Path) -> int:
    """Read the PDF back through pypdf and return the extracted-text length.

    The runner uses pypdf via ``app.foundation.attachments.extractor``; we want to make
    sure what we generated is what the pipeline will see.
    """
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return len("\n\n".join(parts))


def main() -> int:
    here = Path(__file__).parent
    print(f"Writing PDFs to: {here}")
    for kb in _TARGETS_KB:
        path = here / f"attach_{kb}kb.pdf"
        text_chars, file_bytes = _build_pdf(kb, path)
        extracted_chars = _verify_extracted(path)
        print(
            f"  {path.name}: text_chars={text_chars} "
            f"file_bytes={file_bytes} pypdf_extracted={extracted_chars}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
