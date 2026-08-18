"""Matrix personas + the reusable ``build_proposal`` core (Session 13 live) — no network.

Verifies: ``persona_for`` honours the enabled flag / unknown nodes; and that
``build_proposal`` prepends the persona to the system prompt while staying a pure
function over its estimate/analysis dicts (LLM faked).
"""

from __future__ import annotations

import pathlib

from app.domain.graph.agents.proposal import _PROPOSAL_SYSTEM_PROMPT, build_proposal
from app.domain.graph.personas import NODE_PERSONAS, persona_for
from app.domain.graph.schemas import CommercialProposal


def test_persona_for_respects_enabled_and_unknown_nodes():
    assert persona_for("classifier_agent", enabled=False) is None
    assert persona_for("unknown_node", enabled=True) is None
    on = persona_for("analysis_agent", enabled=True)
    assert on is not None and on.startswith("You are the Oracle")
    # The guardrail line is always appended so the character can't break the output.
    assert "never sacrifice correctness" in on


def test_all_llm_nodes_have_a_persona():
    assert set(NODE_PERSONAS) == {
        "classifier_agent",
        "structure_agent",
        "recover_and_handover",
        "analysis_agent",
        "proposal_agent",
    }


class _CapturingWrapper:
    def __init__(self):
        self.system_prompt = None

    def complete_structured(self, *, system_prompt, user_message, response_model, **kwargs):
        self.system_prompt = system_prompt
        return (
            CommercialProposal(
                title="T",
                executive_summary="S",
                scope=["a"],
                total_engineer_days=10,
                body_markdown="# body",
            ),
            {"model": "fake"},
        )


async def test_build_proposal_prepends_persona(monkeypatch):
    wrapper = _CapturingWrapper()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)
    estimate = {"total_engineer_days": 10, "confidence": "high", "modules": []}

    proposal = await build_proposal(estimate, {"summary": "ok"}, persona="You are the Architect.")
    assert isinstance(proposal, CommercialProposal)
    assert wrapper.system_prompt.startswith("You are the Architect.")
    assert _PROPOSAL_SYSTEM_PROMPT in wrapper.system_prompt


async def test_build_proposal_without_persona_uses_base_prompt(monkeypatch):
    wrapper = _CapturingWrapper()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)
    await build_proposal({"modules": []}, {}, persona=None)
    assert wrapper.system_prompt == _PROPOSAL_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Pricing: the business backend computes the money and the agent only quotes it #
# --------------------------------------------------------------------------- #
PRICING = {
    "currency": "EUR",
    "rate_eur_per_hour": 75,
    "contingency_pct": 15,
    "base_eur": 48_488,
    "contingency_eur": 7_273,
    "total_eur": 55_761,
}


class _CapturingUserMessage(_CapturingWrapper):
    def __init__(self):
        super().__init__()
        self.user_message = None

    def complete_structured(self, *, system_prompt, user_message, response_model, **kwargs):
        self.user_message = user_message
        return super().complete_structured(
            system_prompt=system_prompt,
            user_message=user_message,
            response_model=response_model,
            **kwargs,
        )


async def test_pricing_reaches_the_prompt_spelled_out(monkeypatch):
    """Every figure is handed over ready-made, so the model has nothing left to compute."""
    wrapper = _CapturingUserMessage()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)

    await build_proposal({"modules": []}, {}, pricing=PRICING)

    assert "55761 EUR" in wrapper.user_message
    assert "48488 EUR" in wrapper.user_message
    assert "15% = 7273 EUR" in wrapper.user_message
    assert "75 EUR/h" in wrapper.user_message
    assert "do not recompute or reformat" in wrapper.user_message


async def test_preformatted_amounts_are_quoted_verbatim(monkeypatch):
    """The appended tables print "85.301 €". Prose quoting "85,301 EUR" right above them
    reads like a different number, so the caller sends the exact strings to use."""
    wrapper = _CapturingUserMessage()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)

    await build_proposal({"modules": []}, {}, pricing={
        **PRICING,
        "base_display": "48.488 €", "contingency_display": "7.273 €",
        "total_display": "55.761 €", "rate_display": "75 €/h",
    })

    assert "TOTAL: 55.761 €" in wrapper.user_message
    assert "base: 48.488 €" in wrapper.user_message
    assert "hourly_rate: 75 €/h" in wrapper.user_message
    # The raw integers must not leak in alongside the formatted ones.
    assert "55761" not in wrapper.user_message


async def test_without_pricing_the_prompt_says_nothing_about_money(monkeypatch):
    """The pre-pricing path must be untouched: no pricing block, no money in the input."""
    wrapper = _CapturingUserMessage()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)

    await build_proposal({"modules": [], "total_engineer_days": 10}, {}, pricing=None)

    assert "pricing" not in wrapper.user_message
    assert "EUR" not in wrapper.user_message


def test_the_money_rule_allows_quoting_but_forbids_deriving():
    # The old prompt banned prices outright; the new one must still forbid arithmetic.
    assert "EXACTLY as provided" in _PROPOSAL_SYSTEM_PROMPT
    for forbidden in ("NOT compute", "re-round", "prorate", "discounts", "payment schedules"):
        assert forbidden in _PROPOSAL_SYSTEM_PROMPT
    assert "do not mention money at all" in _PROPOSAL_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# The proposal is a client-facing document: no reliability, no tables of its own #
# --------------------------------------------------------------------------- #
async def test_reliability_never_reaches_the_writer(monkeypatch):
    """Confidence and the reliability summary are internal. A model that reads
    "confidence: low" hedges, and hedging belongs on the result screen, not in a proposal."""
    wrapper = _CapturingUserMessage()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)

    await build_proposal(
        {"total_engineer_days": 124, "confidence": "low", "modules": []},
        {"summary": "Solo el 27% de las tareas tiene precedente.",
         "overall_confidence": "low", "grounded_task_ratio": 0.267},
    )

    assert "confidence" not in wrapper.user_message
    assert "reliability" not in wrapper.user_message
    assert "27%" not in wrapper.user_message
    assert "precedente" not in wrapper.user_message
    # The effort headline still gets through.
    assert "total_engineer_days: 124" in wrapper.user_message


def test_the_writer_is_told_not_to_build_tables():
    """The breakdown is composed deterministically downstream; a second, retyped table in
    the same document is worse than none."""
    assert "do NOT write a budget, cost or effort table" in _PROPOSAL_SYSTEM_PROMPT
    assert "appended to your text afterwards" in _PROPOSAL_SYSTEM_PROMPT
    assert "reliability report" not in _PROPOSAL_SYSTEM_PROMPT


async def test_task_names_reach_the_writer(monkeypatch):
    """Asking for 2-3 sentences per module while showing only a rollup is asking the model
    to invent them. The task names are the material it writes from."""
    wrapper = _CapturingUserMessage()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)

    await build_proposal(
        {"total_engineer_days": 12, "modules": [
            {"name": "Autenticación", "tasks": [
                {"name": "OAuth 2.0 / OIDC", "estimated_hours": 24},
                {"name": "Gestión de sesión", "estimated_hours": 16}]}]},
        {},
    )

    assert "Autenticación (2 tasks, 40h)" in wrapper.user_message
    assert "OAuth 2.0 / OIDC" in wrapper.user_message
    assert "Gestión de sesión" in wrapper.user_message
    # Still no money without a pricing block.
    assert "EUR" not in wrapper.user_message


def test_the_prompt_fixes_the_structure_the_language_and_the_bans():
    p = _PROPOSAL_SYSTEM_PROMPT

    # Five sections, always the same, always with prose under them.
    for section in ("Executive summary", "Context and objectives", "Scope by module",
                    "Delivery approach", "Budget note"):
        assert section in p, section
    assert "never a bare heading" in p

    # The document follows the client's own words, not the prompt's language.
    assert "language of the `brief`" in p

    # Reliability is banned outright — an implicit ban was not enough (see run 20).
    assert "no section — and no passing remark — about reliability" in p
    # And no invented calendar: the input has engineer-days, not dates.
    assert "no delivery deadlines" in p


async def test_the_brief_sets_the_language(monkeypatch):
    """The writer only ever saw module names, and the structure agent emits those in English
    even from a Spanish call — so every proposal came out in English. The brief fixes it."""
    wrapper = _CapturingUserMessage()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)

    await build_proposal(
        {"modules": [], "total_engineer_days": 10}, {},
        brief="Somos una fintech y queremos el backend de una app de banca móvil.",
    )

    assert "Somos una fintech" in wrapper.user_message
    assert "do NOT take scope from it" in wrapper.user_message
    # It is context and language, never a second source of money.
    assert "EUR" not in wrapper.user_message


async def test_no_brief_no_brief_block(monkeypatch):
    wrapper = _CapturingUserMessage()
    monkeypatch.setattr("app.dependencies.get_llm_wrapper", lambda: wrapper)

    await build_proposal({"modules": []}, {}, brief=None)

    assert "brief" not in wrapper.user_message


def test_the_raw_transcript_is_preferred_over_the_reformulation():
    """The classifier rewrites the brief into English even from a Spanish call, so the
    reformulation is the wrong source for the proposal's language."""
    src = (pathlib.Path(__file__).parents[3] / "app/domain/graph/agents/proposal.py").read_text()

    assert 'brief=state.get("transcript") or state.get("reformulated_transcript")' in src
