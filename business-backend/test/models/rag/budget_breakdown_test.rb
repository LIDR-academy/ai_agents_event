require "test_helper"

class RagBudgetBreakdownTest < ActiveSupport::TestCase
  def build_run(rate: 75, pct: 15)
    Rag::GraphEstimationRun.create!(
      transcript: "x" * 150, estimation_id: SecureRandom.uuid,
      rate_eur_per_hour: rate, contingency_pct: pct,
      proposal: "# Propuesta\n\nProsa del Arquitecto.",
      estimate: {
        "total_engineer_hours" => 40.5, "total_engineer_days" => 5,
        "modules" => [
          { "name" => "Backend", "tasks" => [ { "name" => "API", "estimated_hours" => 7.5 },
                                              { "name" => "Auth", "estimated_hours" => 13 } ] },
          { "name" => "Front",   "tasks" => [ { "name" => "UI", "estimated_hours" => 20 } ] }
        ]
      }
    )
  end

  test "the TOTAL row matches the pricing base and the run's own hours" do
    b = build_run.budget

    assert_equal 3, b.total_row.count
    assert_in_delta 40.5, b.total_row.hours, 0.001
    assert_in_delta 3_037.5, b.total_row.cost, 0.01
    # And the column above it adds up to the same figure — no rounding drift.
    assert_in_delta b.total_row.cost, b.module_rows.sum(&:cost), 0.01
  end

  test "each module's task rows add up to its subtotal" do
    b = build_run.budget

    b.task_groups.each_with_index do |(name, rows), i|
      assert_in_delta b.module_rows[i].hours, rows.sum(&:hours), 0.001, name
      assert_in_delta b.module_rows[i].cost, rows.sum(&:cost), 0.01, name
    end
  end

  test "half hours survive into the tables" do
    md = build_run.budget.to_markdown

    assert_match "7.5 h", md, "la media hora no puede desaparecer"
    assert_match "20.5 h", md, "subtotal de Backend"
  end

  test "the markdown carries the summary, the price lines and the per-task detail" do
    md = build_run.budget.to_markdown

    assert_match "## Presupuesto", md
    assert_match "### Detalle por tarea", md
    assert_match "#### Backend", md
    assert_match "| **TOTAL**", md
    assert_match "40.5 h × 75 €/h = 3.038 €", md
    assert_match "**Contingencia** (15%)", md
    assert_match(/API/, md)
  end

  test "the tables are padded so they read straight when the markdown is shown raw" do
    rows = build_run.budget.to_markdown.lines.select { |l| l.start_with?("|") }.map(&:chomp)
    summary = rows.first(5) # cabecera + separador + 2 módulos + total

    assert_equal 1, summary.map(&:length).uniq.size, "las filas no están alineadas"
  end

  test "without a rate there is no cost column anywhere" do
    md = build_run(rate: 0).budget.to_markdown

    assert_match "## Presupuesto", md
    assert_no_match(/Coste/, md)
    assert_no_match(/€/, md)
  end

  test "proposal_markdown is prose plus budget, and the stored prose stays clean" do
    run = build_run

    assert_match "Prosa del Arquitecto.", run.proposal_markdown
    assert_match "## Presupuesto", run.proposal_markdown
    # Regenerating must never accumulate tables in the column.
    assert_no_match(/## Presupuesto/, run.proposal)
    assert_equal 1, run.proposal_markdown.scan("## Presupuesto").size
  end

  test "a run with no estimate degrades to the prose alone" do
    run = build_run
    run.update!(estimate: {})

    assert_not run.budget.any?
    assert_equal run.proposal.to_s, run.proposal_markdown
  end
end
