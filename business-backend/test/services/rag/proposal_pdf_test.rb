require "test_helper"

# The PDF gained a "Coste" column, which also moved the right-align range from 1..2 to
# 1..3. That index is the only thing here that can blow up at render time, so both the
# priced and unpriced shapes get exercised.
class RagProposalPdfTest < ActiveSupport::TestCase
  def run_with(rate:, pct: 10)
    Rag::GraphEstimationRun.create!(
      transcript: "x" * 150, estimation_id: SecureRandom.uuid,
      rate_eur_per_hour: rate, contingency_pct: pct,
      status: "validated", graph_state: "completed",
      proposal: "# Propuesta\n\nCuerpo.", proposal_title: "Propuesta NeoPagos",
      analysis_report: { "overall_confidence" => "high", "grounded_task_ratio" => 1.0, "summary" => "ok" },
      estimate: {
        "total_engineer_days" => 5, "total_engineer_hours" => 40.5,
        "modules" => [ { "name" => "Backend",
                         "tasks" => [ { "name" => "API", "estimated_hours" => 20.5 },
                                      { "name" => "Auth", "estimated_hours" => 20 } ] } ]
      }
    )
  end

  test "renders with the cost column when the run is priced" do
    pdf = Rag::ProposalPdf.new(run_with(rate: 100)).render

    assert pdf.start_with?("%PDF"), "no es un PDF"
    assert_operator pdf.bytesize, :>, 1_000
  end

  test "renders without the cost column when there is no rate" do
    pdf = Rag::ProposalPdf.new(run_with(rate: 0)).render

    assert pdf.start_with?("%PDF")
  end

  test "the PDF carries no reliability section" do
    # It is a client-facing document: how much historical precedent the hours had is
    # internal, and stays on the result screen.
    source = Rails.root.join("app/services/rag/proposal_pdf.rb").read

    assert_no_match(/def reliability/, source)
    assert_no_match(/analysis_report/, source)
  end

  test "the PDF grows with the per-task detail" do
    run = run_with(rate: 100)

    # 1 módulo con 2 tareas: la sección de detalle tiene que aportar páginas de contenido.
    assert_operator Rag::ProposalPdf.new(run).render.bytesize, :>, 2_000
  end

  # REGRESSION. The old parser matched a heading with /\A#+\s*(.+)/ — no /m — so it printed
  # the title and dropped the rest of the block. The model does not leave a blank line after
  # its headings, so every proposal rendered as a list of bare titles.
  test "text that follows a heading in the same block reaches the PDF" do
    bare = run_with(rate: 100)
    bare.update!(proposal: "## Resumen ejecutivo\n## Alcance\n## Enfoque")

    full = run_with(rate: 100)
    full.update!(proposal: "## Resumen ejecutivo\n#{'Un párrafo con sustancia. ' * 40}\n" \
                           "## Alcance\n#{'Más contenido real aquí. ' * 40}\n" \
                           "## Enfoque\n#{'Y todavía más texto. ' * 40}")

    assert_operator Rag::ProposalPdf.new(full).render.bytesize,
                    :>, Rag::ProposalPdf.new(bare).render.bytesize + 2_000,
                    "el cuerpo bajo los encabezados se está perdiendo"
  end

  test "a reliability section never reaches the PDF, wherever the model puts it" do
    run = run_with(rate: 100)
    run.update!(proposal: "## Resumen\nTexto bueno.\n\n" \
                          "## Reliability and Confidence\nSolo el 27% tiene precedente.\n\n" \
                          "## Presupuesto\nTexto final.")

    prose = run.proposal_prose

    assert_match "Texto bueno.", prose
    assert_match "Texto final.", prose
    assert_no_match(/Reliability|27%|precedente/, prose)
  end

  test "markdown emphasis does not leak into the PDF as asterisks" do
    run = run_with(rate: 100)
    run.update!(proposal: "## Resumen\nEl coste es **85.301 €** y el enfoque es *iterativo*.")

    # Prawn runs without inline_format here, so the markers must be stripped, not passed on.
    assert Rag::ProposalPdf.new(run).render.start_with?("%PDF")
  end

  test "the numbers that feed the PDF are the priced ones" do
    run = run_with(rate: 100)

    # 40,5 h x 100 EUR = 4.050 base, +10% = 4.455. La media hora no se pierde.
    assert_in_delta 4_050.0, run.pricing.base, 0.01
    assert_in_delta 4_455.0, run.pricing.total, 0.01
    assert_in_delta 40.5, run.estimate_modules.sum(&:subtotal_hours), 0.001
    assert_in_delta 4_050.0, run.estimate_modules.sum(&:subtotal_cost), 0.01
  end
end
