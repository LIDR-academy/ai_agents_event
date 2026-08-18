"""``proposal_agent`` (BONUS) — drafts a commercial proposal from the estimate.

Runs only after the final human gate has VALIDATED the estimate (and the human asked
for a proposal). It writes a client-facing proposal grounded strictly in the validated
estimate — no new scope, no invented numbers. Gated by ``GRAPH_PROPOSAL_ENABLED`` and
the gate-2 ``want_proposal`` flag (see ``build.route_after_gate2``).
"""

from __future__ import annotations

import asyncio

import logfire
import structlog

from app.config import get_settings
from app.domain.graph.personas import persona_for
from app.domain.graph.schemas import CommercialProposal

log = structlog.get_logger()

# The money rule is deliberately narrow. The previous version banned prices outright,
# which was right while nothing computed them; now the business backend hands over a
# pricing block, and the agent must be able to QUOTE it without gaining licence to do
# arithmetic. Quoting is safe, deriving is not: a model that starts rounding, prorating
# or projecting discounts produces a number nobody can trace back to the hours.
_PROPOSAL_SYSTEM_PROMPT = (
    "You are a delivery lead writing a commercial proposal for a client, based STRICTLY on "
    "a validated software estimate (modules → tasks). Fill `title`, `executive_summary`, "
    "`scope` and `total_engineer_days`, and write the full document in `body_markdown`.\n"
    "LANGUAGE: write EVERYTHING in the language of the `brief` when one is given — that is "
    "the client's own words. Otherwise follow the language of the module names.\n"
    "STRUCTURE: `body_markdown` has exactly these five sections, in this order, each with "
    "real prose under its heading — never a bare heading. Use `##` for these five headings "
    "and `###` for any per-module subsection. TRANSLATE the headings into the language you "
    "are writing in; do not leave them in English:\n"
    "  1. Executive summary — 3 to 5 sentences: what is being built and the headline effort.\n"
    "  2. Context and objectives — two paragraphs on the problem this solves for the client.\n"
    "  3. Scope by module — for EVERY module, 2-3 sentences saying what it covers and why it "
    "is needed, written FROM its task list. Describe the work; do NOT enumerate the tasks, "
    "a detailed table is appended afterwards.\n"
    "  4. Delivery approach — how the work is tackled and in what order.\n"
    "  5. Budget note — one paragraph introducing the breakdown that follows.\n"
    "Do NOT invent scope or numbers not present in the input.\n"
    "FORBIDDEN: no section — and no passing remark — about reliability, confidence, risk, "
    "uncertainty or how solid the estimate is. That is an internal signal and it does not "
    "belong in a client document. Also no dates, no calendar and no delivery deadlines: the "
    "input has engineer-days, not a schedule, so any timeline would be invented.\n"
    "TABLES: do NOT write a budget, cost or effort table of any kind. A precise breakdown "
    "by module and by task is appended to your text afterwards, so a table here would only "
    "duplicate it — and would do so with figures you retyped. Refer to the budget in prose "
    "and let the appended section carry the numbers.\n"
    "MONEY: if — and only if — a `pricing` block is given, you may state those figures "
    "EXACTLY as provided, and you must copy the total into `total_price_eur`. You must "
    "NOT compute, re-round, prorate or otherwise derive any monetary figure: no per-module "
    "prices, no discounts, no payment schedules, no taxes, no currency conversion. If no "
    "pricing block is given, do not mention money at all and leave `total_price_eur` null. "
    "Keep it honest and client-ready."
)


def _pricing_lines(pricing: dict) -> list[str]:
    """The pricing block, spelled out so the model has nothing left to calculate."""
    currency = pricing.get("currency", "EUR")

    # Prefer the pre-formatted strings when the caller sends them: the tables appended to
    # this document write "85.301 €", and prose quoting "85,301 EUR" two paragraphs above
    # them reads like a different figure.
    def amount(key: str) -> str:
        display = pricing.get(key + "_display")
        return display if display else "{} {}".format(pricing.get(key + "_eur"), currency)

    rate = pricing.get("rate_display")
    if not rate:
        rate = "{} {}/h".format(pricing.get("rate_eur_per_hour"), currency)

    lines = [
        "pricing (quote these strings EXACTLY as written; do not recompute or reformat):",
        f"  hourly_rate: {rate}",
        f"  base: {amount('base')}",
    ]
    if pricing.get("contingency_pct"):
        lines.append(f"  contingency: {pricing.get('contingency_pct')}% = {amount('contingency')}")
    lines.append(f"  TOTAL: {amount('total')}")
    return lines


def _proposal_input(
    estimate: dict,
    analysis_report: dict,
    pricing: dict | None = None,
    brief: str | None = None,
) -> str:
    """What the writer sees. Deliberately NOT the reliability report.

    ``confidence`` and the reliability summary used to be here. They are internal quality
    signals — how much historical precedent the hours had — and a model that reads
    "confidence: low" starts hedging in a document meant to win the work. The reliability
    report is still shown on the result screen, where it belongs; ``analysis_report`` stays
    in the signature because both callers pass it.
    """
    lines = []
    if brief:
        # The client's own words, for LANGUAGE and context only. Without it the writer sees
        # nothing but the module names — and those come out of the structure agent in
        # English even from a Spanish call, so every proposal was written in English.
        # Scope still comes from the modules: this is not a second source of requirements.
        lines += [
            "brief (client's own words — use it for the LANGUAGE to write in and for "
            "context; do NOT take scope from it):",
            f"  {brief.strip()[:1200]}",
            "",
        ]
    lines.append(f"total_engineer_days: {estimate.get('total_engineer_days')}")
    if pricing:
        lines.extend(_pricing_lines(pricing))
    # Task NAMES, not just the rollup. Asking for 2-3 sentences per module while showing
    # only "Backend: 7 tasks, 173h" is asking the model to make them up; the names are the
    # material it writes from. No figures here — the money lives in the pricing block above.
    lines.append("modules (write from these; do not list the tasks verbatim):")
    for module in estimate.get("modules") or []:
        tasks = module.get("tasks") or []
        hours = sum(t.get("estimated_hours") or 0 for t in tasks)
        lines.append(f"  - {module.get('name')} ({len(tasks)} tasks, {hours}h)")
        for task in tasks:
            lines.append(f"      · {task.get('name')}")
    return "\n".join(lines)


async def build_proposal(
    estimate: dict,
    analysis_report: dict,
    *,
    persona: str | None = None,
    pricing: dict | None = None,
    brief: str | None = None,
) -> CommercialProposal:
    """Draft a full ``CommercialProposal`` from a validated estimate.

    The reusable core of the proposal agent: pure over its ``estimate`` /
    ``analysis_report`` dict inputs, so it powers both the graph node AND the
    standalone ``POST …/graph/{id}/proposal`` endpoint (generate a proposal after the
    run completed, without re-running the graph). ``persona`` is prepended to the
    system prompt when the agent is played in character.
    """
    settings = get_settings()
    from app.dependencies import get_llm_wrapper

    wrapper = get_llm_wrapper()
    system_prompt = f"{persona}\n\n{_PROPOSAL_SYSTEM_PROMPT}" if persona else _PROPOSAL_SYSTEM_PROMPT
    user_message = _proposal_input(
        estimate or {}, analysis_report or {}, pricing or None, brief or None
    )
    proposal, _meta = await asyncio.to_thread(
        wrapper.complete_structured,
        system_prompt=system_prompt,
        user_message=user_message,
        response_model=CommercialProposal,
        model_override=settings.GRAPH_PROPOSAL_MODEL,
    )
    return proposal


async def proposal_agent(state: dict) -> dict:
    """Validated estimate → commercial proposal (Markdown). Graph node wrapper."""
    with logfire.span("node: proposal_agent"):
        persona = persona_for(
            "proposal_agent", enabled=get_settings().GRAPH_PERSONAS_ENABLED
        )
        proposal = await build_proposal(
            state.get("estimate") or {},
            state.get("analysis_report") or {},
            persona=persona,
            pricing=state.get("pricing"),
            # The RAW transcript, not the reformulation: the classifier rewrites the brief
            # into English even from a Spanish call ("Project Brief: Mobile Banking…"), so
            # using it here made every proposal English. The raw text is the client's own
            # words, which is exactly what should set the language.
            brief=state.get("transcript") or state.get("reformulated_transcript"),
        )
        log.info(
            "agent_proposal_done",
            title=proposal.title,
            scope=len(proposal.scope),
            quoted_price_eur=proposal.total_price_eur,
        )
        return {"proposal": proposal.body_markdown}
