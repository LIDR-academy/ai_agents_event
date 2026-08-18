"""The structural chunker surfaces the optional `module` field (S09 task corpus)."""

from __future__ import annotations

from app.generation.rag.chunking.structural import component_metadata, render_component_text
from app.generation.rag.schemas import Budget, BudgetComponent, ClientMetadata


def _budget(component: BudgetComponent) -> Budget:
    return Budget(
        budget_id="B1",
        client_metadata=ClientMetadata(name="Acme", sector="finance", country="ES"),
        project_summary="P",
        main_technology="python",
        year=2024,
        total_estimated_hours=component.estimated_hours,
        components=[component],
    )


def test_module_appears_in_text_and_metadata():
    component = BudgetComponent(
        component_id="PAY-001",
        name="Payment gateway integration",
        description="Card payments via a PSP.",
        module="Payments & Billing",
        tech_stack=["stripe"],
        estimated_hours=24,
        complexity="high",
    )
    text = render_component_text(_budget(component), component)
    assert "Module: Payments & Billing" in text
    assert component_metadata(_budget(component), component)["module"] == "Payments & Billing"


def test_no_module_keeps_text_clean_and_metadata_none():
    component = BudgetComponent(
        component_id="X-001",
        name="Generic component",
        description="No module tag.",
        tech_stack=[],
        estimated_hours=10,
        complexity="low",
    )
    text = render_component_text(_budget(component), component)
    assert "Module:" not in text
    assert component_metadata(_budget(component), component)["module"] is None
