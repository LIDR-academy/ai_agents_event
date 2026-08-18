"""Node-internal LLM I/O models for the graph.

These are the ``response_model``s the structured-output nodes hand to
``LLMWrapper.complete_structured`` (Instructor validates + re-prompts the LLM
against them). They are deliberately kept OUT of ``app/domain/schemas`` — that
package is the external contract with Rails; these are private plumbing of the
graph nodes. The public request/response contract lives in
``app/domain/schemas/graph_estimation.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]


class RequirementsExtraction(BaseModel):
    """Output of ``extract_requirements``: the flat list of requirements."""

    requirements: list[str] = Field(
        default_factory=list,
        description="Concrete, atomic functional/technical requirements the client "
        "wants, one per item, in concise technical English. Ignore small talk.",
    )


class ComponentModel(BaseModel):
    """One classified component (mirrors the ``Component`` TypedDict)."""

    name: str = Field(description="Short component name, e.g. 'Business backend API'.")
    category: str = Field(
        description="Coarse component category, e.g. 'backend', 'integration', "
        "'mobile', 'analytics', 'frontend', 'infrastructure'."
    )


class ComponentClassification(BaseModel):
    """Output of ``classify_components``: requirements grouped into components."""

    components: list[ComponentModel] = Field(default_factory=list)


class ComponentEstimate(BaseModel):
    """A single component's consolidated effort in the final estimate."""

    name: str
    engineer_days: int | None = Field(
        default=None,
        ge=0,
        description="Consolidated effort in engineer-days, as an INTEGER, in THIS "
        "field (not only in the rationale). Set it to the rounded median of the "
        "component's references converted to days. Use null ONLY when the component "
        "has NO references.",
    )
    rationale: str = Field(
        description="One line on how the number was derived from the references."
    )


class ConsolidatedEstimate(BaseModel):
    """Output of ``generate_estimate``: the structured estimate.

    Grounded in the ``budget_matches`` the graph accumulated (historical hours), so
    the numbers trace back to retrieved references rather than being invented.
    """

    components: list[ComponentEstimate] = Field(default_factory=list)
    total_engineer_days: int | None = Field(default=None, ge=0)
    confidence: Confidence = "medium"
    reasoning: str = Field(description="Short explanation of the consolidation.")


# --------------------------------------------------------------------------- #
# Session 13 (live) — the multi-agent nodes' LLM I/O models                   #
# --------------------------------------------------------------------------- #
Complexity = Literal["low", "medium", "high"]


class ComplexityClassification(BaseModel):
    """Output of ``classifier_agent``: complexity + a reformulated brief.

    The classifier reads the raw, messy meeting transcript and does two things at
    once: it judges HOW COMPLEX the estimation will be (which the graph maps to the
    structure agent's reasoning effort) and it REFORMULATES the transcript into a
    clean, self-contained project brief the rest of the flow can consume.
    """

    complexity: Complexity = Field(
        description="How complex the estimation is. 'low' = one simple component; "
        "'medium' = a few components; 'high' = many dispares components / integrations."
    )
    reformulated_transcript: str = Field(
        min_length=1,
        description="The transcript rewritten as a clean, self-contained project "
        "brief in technical English: the components the client wants, their scope and "
        "constraints, with the small talk and digressions removed. No invented scope.",
    )
    reasoning: str = Field(description="One line on why that complexity was assigned.")


class WeakPoint(BaseModel):
    """One weakness the analysis agent flags for the human's final review."""

    area: str = Field(description="Module/task or cross-cutting concern the weakness touches.")
    issue: str = Field(description="What is uncertain, ungrounded or contradictory.")
    severity: Literal["low", "medium", "high"] = "medium"


class ReliabilityReport(BaseModel):
    """Output of ``analysis_agent``: a data-reliability read of the estimate.

    Validates the hours the retrieval agent grounded and writes a short report that
    tells the human, before the final gate, HOW MUCH to trust the numbers and WHERE
    the estimate is weak (ungrounded tasks, contradictory analogs, low reliability).
    """

    overall_confidence: Literal["low", "medium", "high"] = Field(
        description="Overall confidence in the estimate as a whole."
    )
    grounded_task_ratio: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of tasks that got hours from a historical match (0..1).",
    )
    weak_points: list[WeakPoint] = Field(
        default_factory=list,
        description="The specific soft spots the human should check or complete.",
    )
    summary: str = Field(description="A short prose read of the estimate's reliability.")


class CommercialProposal(BaseModel):
    """Output of ``proposal_agent`` (bonus): a client-facing commercial proposal."""

    title: str = Field(description="Proposal title, e.g. the project name.")
    executive_summary: str = Field(description="2-4 sentences a client executive would read.")
    scope: list[str] = Field(
        default_factory=list, description="Bullet scope: the modules/deliverables included."
    )
    total_engineer_days: int | None = Field(
        default=None, ge=0, description="Headline effort, echoed from the validated estimate."
    )
    total_price_eur: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The total price, echoed VERBATIM from the pricing block when one was supplied. "
            "It exists so the caller can verify the model quoted the figure it was given "
            "instead of deriving its own; leave it null when no pricing was provided."
        ),
    )
    body_markdown: str = Field(
        description="The full proposal as Markdown, grounded ONLY in the validated estimate."
    )


# --------------------------------------------------------------------------- #
# Session 14 — the supervisor's routing decision                              #
# --------------------------------------------------------------------------- #
SupervisorTarget = Literal[
    "requirements_extractor",
    "budget_searcher",
    "estimate_generator",
    "coherence_validator",
    "finish",
]


class SupervisorDecision(BaseModel):
    """Output of the hand-built supervisor: WHO acts next, and WHY.

    Constraining ``next_agent`` to a ``Literal`` is what keeps an LLM-driven router
    safe enough to ship: the model cannot invent a destination, only choose among the
    five the graph knows about. (``LLMWrapper.complete_structured`` exposes no
    ``temperature``, so the schema — plus the legality guard in ``supervisor.py`` —
    is where determinism comes from.)

    ``reason`` is not decoration. It is written into ``routing_history`` and is the
    thing that makes a routing decision auditable; a supervisor that cannot say why it
    routed somewhere is indistinguishable from a hard-coded pipeline wearing a costume.
    """

    next_agent: SupervisorTarget = Field(
        description="The specialist that must act next, or 'finish' when the estimate "
        "has been produced and validated."
    )
    reason: str = Field(
        description="One line: what the state already contains, what is still missing, "
        "and why THIS agent is the one that can produce it."
    )
    confidence: Confidence = Field(
        default="medium", description="How sure the router is about this hand-over."
    )


# --------------------------------------------------------------------------- #
# Session 14 (LIVE) — the competition pattern                                 #
# --------------------------------------------------------------------------- #
Stance = Literal["conservative", "aggressive"]


class EstimateProposal(BaseModel):
    """One competing estimate produced from a single stance.

    Two estimators produce these INDEPENDENTLY from substantively different priors —
    not "the same estimate but more/less cautious". The divergence between the two
    totals is the signal we care about: a wide spread means the project carries
    structural uncertainty the model cannot resolve on its own, and that is exactly the
    moment a human should look. The number alone is cheap; the disagreement is the
    information.
    """

    stance: Stance = Field(description="Which estimator produced this proposal.")
    total_engineer_days: int = Field(
        ge=0, description="This stance's headline effort in engineer-days."
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="The load-bearing assumptions this number rests on.",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="What could make the real effort diverge from this number.",
    )
    reasoning: str = Field(description="One paragraph: how the number was reached.")


class SynthesizedEstimate(BaseModel):
    """The synthesizer's output: a RANGE, never an average.

    Given the two competing proposals and their arithmetic divergence, the synthesizer
    produces a bracket plus the assumptions that drive it and the questions that, once
    answered, would collapse the range. Averaging two numbers throws away the very
    information the competition surfaced — so the prompt forbids it explicitly.
    """

    low: int = Field(ge=0, description="Lower bound of the estimate range (engineer-days).")
    high: int = Field(ge=0, description="Upper bound of the estimate range (engineer-days).")
    driving_assumptions: list[str] = Field(
        default_factory=list,
        description="The assumptions that most move the number between low and high.",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Questions whose answers would narrow the range.",
    )
    confidence: Confidence = Field(
        default="medium", description="Confidence in the range as a whole."
    )
    reasoning: str = Field(
        description="Short prose on how the bracket was set — explicitly NOT an average."
    )
