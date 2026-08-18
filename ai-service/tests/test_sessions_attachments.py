"""Integration test: a PDF attachment reaches the LLM as part of the user message."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeLLMWrapper


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


VALID_FORM_FIELDS = {
    "transcript": "We need an estimation; refer to the attached spec for technical details.",
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table",
}


def test_docx_attachment_contents_reach_llm(
    conversational_client: tuple[TestClient, object],
    fake_wrapper: FakeLLMWrapper,
) -> None:
    client, _store = conversational_client
    fake_wrapper.add_turn()

    session_id = client.post("/sessions").json()["session_id"]

    docx_payload = _docx_bytes(
        ["Annexed spec for Nimbus CRM.", "Stack: React + Postgres", "Phase 1: contacts module."]
    )

    response = client.post(
        f"/sessions/{session_id}/estimate",
        data=VALID_FORM_FIELDS,
        files=[
            (
                "attachments",
                ("spec.docx", docx_payload,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            )
        ],
    )
    assert response.status_code == 200, response.text

    # The first chat_call is the estimation; inspect its messages.
    estimation_call = fake_wrapper.chat_calls[0]
    last_user_message = next(
        m for m in reversed(estimation_call["messages"]) if m["role"] == "user"
    )
    assert "--- attachment: spec.docx ---" in last_user_message["content"]
    assert "Nimbus CRM" in last_user_message["content"]
    assert "Phase 1: contacts module" in last_user_message["content"]


def test_pdf_attachment_contents_reach_llm(
    conversational_client: tuple[TestClient, object],
    fake_wrapper: FakeLLMWrapper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock pypdf to keep the test deterministic without a real binary."""
    client, _store = conversational_client
    fake_wrapper.add_turn()

    fake_pages = [
        SimpleNamespace(extract_text=lambda: "Nimbus PDF spec line 1"),
        SimpleNamespace(extract_text=lambda: "Nimbus PDF spec line 2"),
    ]
    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", lambda _stream: SimpleNamespace(pages=fake_pages))

    session_id = client.post("/sessions").json()["session_id"]

    response = client.post(
        f"/sessions/{session_id}/estimate",
        data=VALID_FORM_FIELDS,
        files=[("attachments", ("proposal.pdf", b"%PDF-fake-bytes", "application/pdf"))],
    )
    assert response.status_code == 200, response.text

    estimation_call = fake_wrapper.chat_calls[0]
    last_user_message = next(
        m for m in reversed(estimation_call["messages"]) if m["role"] == "user"
    )
    assert "--- attachment: proposal.pdf ---" in last_user_message["content"]
    assert "Nimbus PDF spec line 1" in last_user_message["content"]
    assert "Nimbus PDF spec line 2" in last_user_message["content"]


def test_unsupported_attachment_returns_415(
    conversational_client: tuple[TestClient, object],
    fake_wrapper: FakeLLMWrapper,
) -> None:
    client, _ = conversational_client
    fake_wrapper.add_turn()
    session_id = client.post("/sessions").json()["session_id"]

    response = client.post(
        f"/sessions/{session_id}/estimate",
        data=VALID_FORM_FIELDS,
        files=[("attachments", ("photo.png", b"\x89PNG\r\n", "image/png"))],
    )
    assert response.status_code == 415
    assert response.json()["detail"]["reason"] == "unsupported_attachment"


def test_no_attachments_still_works(
    conversational_client: tuple[TestClient, object],
    fake_wrapper: FakeLLMWrapper,
) -> None:
    client, _ = conversational_client
    fake_wrapper.add_turn()

    session_id = client.post("/sessions").json()["session_id"]
    response = client.post(f"/sessions/{session_id}/estimate", data=VALID_FORM_FIELDS)
    assert response.status_code == 200
    estimation_call = fake_wrapper.chat_calls[0]
    last_user_message = next(
        m for m in reversed(estimation_call["messages"]) if m["role"] == "user"
    )
    assert "--- attachment:" not in last_user_message["content"]
