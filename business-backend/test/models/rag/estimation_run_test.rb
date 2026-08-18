require "test_helper"

class Rag::EstimationRunTest < ActiveSupport::TestCase
  def chunk(id)
    { "id" => id, "content" => "c#{id}", "sector" => "finance",
      "project_year" => 2024, "chunk_type" => "line_item", "distance" => 0.4 }
  end

  test "requires a transcript" do
    assert_not Rag::EstimationRun.new(transcript: "").valid?
    assert Rag::EstimationRun.new(transcript: "hello").valid?
  end

  test "per-stage views round-trip from JSONB" do
    run = Rag::EstimationRun.create!(
      transcript: "t",
      reformulation: { "query" => { "function" => "store", "sector" => "ecommerce",
                                    "technologies" => [ "Stripe" ] }, "search_text" => "store" },
      generation: { "estimate" => { "confidence" => "high", "reasoning" => "r",
                    "modules" => [ { "name" => "Auth", "tasks" => [
                      { "name" => "OAuth", "sources" => [] },
                      { "name" => "RBAC", "sources" => [] } ] } ] },
                    "fabricated_source_ids" => [], "coherent" => true }
    )

    assert_equal "ecommerce", run.reformulation_view.query.sector
    assert_equal [ "Stripe" ], run.reformulation_view.query.technologies
    # Structure-only generation: tasks present, hours null, clean grounding.
    assert_equal 2, run.generation_view.estimate.modules.first.tasks.size
    assert run.generation_view.grounding_clean?
  end

  test "blank stage columns return nil views" do
    run = Rag::EstimationRun.create!(transcript: "t")
    assert_nil run.reformulation_view
    assert_nil run.generation_view
  end

  test "clear_downstream! nulls later stage columns" do
    run = Rag::EstimationRun.create!(
      transcript: "t",
      reformulation: { "query" => {}, "search_text" => "s" },
      generation: { "estimate" => { "confidence" => "high", "reasoning" => "r" } },
      structure: { "modules" => [ { "name" => "Auth", "tasks" => [ { "name" => "OAuth" } ] } ] },
      task_hours: { "tasks" => [] }
    )

    run.clear_downstream!("generation")
    run.reload
    assert run.generation.present?, "generation itself is kept"
    assert run.structure.blank?, "structure cleared"
    assert run.task_hours.blank?, "task_hours cleared"
  end

  test "insufficient estimate exposes no numbers" do
    run = Rag::EstimationRun.create!(
      transcript: "t",
      generation: { "estimate" => { "confidence" => "insufficient", "reasoning" => "r",
                    "insufficient_context_explanation" => "nothing relevant" },
                    "fabricated_source_ids" => [], "coherent" => true }
    )
    estimate = run.generation_view.estimate
    assert estimate.insufficient?
    assert_nil estimate.total_engineer_days
    assert estimate.modules.empty?
  end
end
