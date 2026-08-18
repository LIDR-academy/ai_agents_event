"""The agent's reason→act→observe trace (Session 12).

Lives in ``domain/schemas`` — NOT in ``generation/agentic`` — on purpose: the
trace is a shared audit contract. The agentic loop *produces* it, but the RAG
response schemas (``GenerateResult`` / ``TaskHoursResult``) *carry* it so the
Rails wizard can render the STEP N trace alongside the structure and the hours.
Both ``generation`` siblings may import ``domain/schemas``, so parking the trace
here lets ``rag`` embed it without importing ``agentic`` (a forbidden
cross-sibling import) — see ``ARCHITECTURE.md`` §7.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

# Strip NUL and other control characters a model may occasionally emit inside a
# malformed unicode escape (e.g. a stray NUL byte in a tool argument), so a
# model glitch never poisons the readable trace / the committed deliverable.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class AgentStep(BaseModel):
    """One reason→act→observe step of the loop."""

    step: int = Field(ge=1)
    reasoning_summary: str | None = Field(
        default=None,
        description="Model reasoning summary for this step (Responses API reasoning summary).",
    )
    tool: str = Field(description="Name of the invoked tool.")
    tool_args: dict[str, Any] = Field(description="Arguments the model passed to the tool.")
    observation: str = Field(description="Human-readable summary of the tool result.")


class AgentTrace(BaseModel):
    """Ordered record of everything the agent did, for auditing and the deliverable."""

    steps: list[AgentStep] = Field(default_factory=list)

    def render(self) -> str:
        """Render the trace in the ``STEP N`` console format from the statement."""
        if not self.steps:
            return "(no tool steps — the agent answered without calling any tool)"
        blocks: list[str] = []
        for s in self.steps:
            reasoning = s.reasoning_summary or "(no reasoning summary emitted)"
            args = _CONTROL_CHARS.sub("", json.dumps(s.tool_args, ensure_ascii=False, default=str))
            blocks.append(
                f"STEP {s.step}\n"
                f"  reasoning:   {reasoning}\n"
                f"  action:      {s.tool}({args})\n"
                f"  observation: {s.observation}"
            )
        return "\n\n".join(blocks)
