"""Input guardrails: three layers run before the LLM is called.

1. Moderation (OpenAI Moderation API): blocks hate, violence, sexual content, etc.
2. Prompt-injection heuristics (regex over known patterns): cheap second line of
   defence against "ignore previous instructions"-style attacks.
3. PII heuristics (regex over emails, phones, IBANs): not exhaustive on purpose,
   it's a demo of the *pattern*, not a compliance-grade redactor.

Policy: all three raise ``InputGuardrailViolation`` (exception, never fix-and-retry).
Reasons live in the ``reason`` attribute so the HTTP layer can pick a status code
and the cliente can render an appropriate message.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import structlog

log = structlog.get_logger()


Reason = Literal["moderation", "prompt_injection", "pii"]


class InputGuardrailViolation(Exception):
    """Raised by ``check_input`` when one of the input layers rejects the description."""

    def __init__(self, message: str, *, reason: Reason) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


# --- Prompt injection patterns ----------------------------------------------

_PROMPT_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"ignore\s+(previous|prior|all|the)\s+(instructions?|prompts?|rules?)", re.IGNORECASE
    ),
    re.compile(r"</?\s*(system|instructions?|prompt)\s*>", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*[:.\-]", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|previous)", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(
        r"\bdisregard\b.{0,40}\b(instructions?|prompts?|rules?|context|previous|prior)",
        re.IGNORECASE | re.DOTALL,
    ),
]

# --- PII patterns -----------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
# Phone: international +XX with 9-12 digits, or national 9+ consecutive digits.
# Conservative on purpose so we don't flag dates / version numbers.
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\d[\s.-]?){9,12}\d")


def check_input(description: str, *, openai_client: Any | None = None) -> None:
    """Run the three input layers in order and raise on the first violation.

    ``openai_client`` is optional so tests can opt out of the moderation call.
    In production it should be set; in unit tests we typically pass ``None`` and
    only exercise the regex layers.
    """
    if openai_client is not None:
        _check_moderation(description, openai_client)
    _check_prompt_injection(description)
    _check_pii(description)


def _check_moderation(description: str, openai_client: Any) -> None:
    try:
        response = openai_client.moderations.create(input=description)
    except Exception as exc:  # noqa: BLE001 — network/auth failures fail open with a log
        log.warning(
            "moderation_call_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return

    result = response.results[0]
    if getattr(result, "flagged", False):
        categories = _extract_flagged_categories(result)
        log.info("moderation_flagged", categories=categories)
        raise InputGuardrailViolation(
            f"Input flagged by moderation: {', '.join(categories) or 'unspecified'}",
            reason="moderation",
        )


def _extract_flagged_categories(result: Any) -> list[str]:
    categories = getattr(result, "categories", None)
    if categories is None:
        return []
    # Pydantic v2 / OpenAI client v1 both expose ``model_dump`` or ``dict``.
    data = categories.model_dump() if hasattr(categories, "model_dump") else categories.__dict__
    return [name for name, flagged in data.items() if flagged]


def _check_prompt_injection(description: str) -> None:
    for pattern in _PROMPT_INJECTION_PATTERNS:
        match = pattern.search(description)
        if match:
            log.info(
                "prompt_injection_detected", pattern=pattern.pattern, match=match.group(0)[:80]
            )
            raise InputGuardrailViolation(
                f"Suspicious instruction-like text detected: {match.group(0)[:80]!r}",
                reason="prompt_injection",
            )


def _check_pii(description: str) -> None:
    if _EMAIL_RE.search(description):
        raise InputGuardrailViolation(
            "Email address detected in description — please remove personal data.",
            reason="pii",
        )
    if _IBAN_RE.search(description):
        raise InputGuardrailViolation(
            "IBAN detected in description — please remove personal data.",
            reason="pii",
        )
    if _PHONE_RE.search(description):
        raise InputGuardrailViolation(
            "Phone number detected in description — please remove personal data.",
            reason="pii",
        )
