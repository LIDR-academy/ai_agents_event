"""The multi-agent flow (Session 13, live).

Where the pre-exercise graph was five *component-level* nodes wired straight
through, the live session re-expresses the estimation flow as a pipeline of
SPECIALISED AGENTS with explicit handovers and two human gates:

    classifier_agent            complexity + reformulation
        │ Command(goto)  ── explicit handover ──▶
    structure_agent             modules → tasks (reuses run_structure_agent, S12)
        │ edge
    human_gate_structure        ⏸ interrupt()  ── HUMAN GATE 1 (review the breakdown)
        │ Send fan-out (one branch per approved task)
    estimate_task_hours × N     per-task hours from the vector DB (reuses estimate_one, S10)
        │ edge (join)
    recover_and_handover        agentic recovery of doubtful tasks + build the estimate
        │ Command(goto)  ── explicit handover ──▶
    analysis_agent              reliability report + weak points
        │ edge
    human_gate_analysis         ⏸ interrupt()  ── HUMAN GATE 2 (final review + complete)
        │ conditional
    proposal_agent (bonus)      commercial proposal ──▶ END

Each agent is a node (some wrap a whole S12 reasoning loop). The two ``Command``
handovers pass control AND state to the receiver; the two ``interrupt()`` gates
pause the run — the ``AsyncPostgresSaver`` checkpointer keeps the state alive across
a pause that may last minutes or days, and the business backend resumes it with
``Command(resume=...)``.

Every reusable retrieval/agent primitive is imported at MODULE level so tests and
the offline runner can monkeypatch it (the same seam ``nodes.py`` uses).
"""

from __future__ import annotations

from app.domain.graph.agents.analysis import analysis_agent
from app.domain.graph.agents.classifier import classifier_agent
from app.domain.graph.agents.gates import human_gate_analysis, human_gate_structure
from app.domain.graph.agents.hours import estimate_task_hours, recover_and_handover
from app.domain.graph.agents.proposal import proposal_agent
from app.domain.graph.agents.structure import structure_agent

__all__ = [
    "classifier_agent",
    "structure_agent",
    "human_gate_structure",
    "estimate_task_hours",
    "recover_and_handover",
    "analysis_agent",
    "human_gate_analysis",
    "proposal_agent",
]
