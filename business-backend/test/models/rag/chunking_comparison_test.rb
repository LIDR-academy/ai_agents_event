require "test_helper"

class RagChunkingComparisonTest < ActiveSupport::TestCase
  def minimal_payload
    {
      "stats_per_strategy" => {
        "structural" => {
          "strategy" => "structural", "n_chunks" => 60,
          "token_distribution" => { "min" => 40, "p50" => 70.0, "p95" => 120.0, "max" => 1391 },
          "n_orphan_chunks" => 0, "n_obese_chunks" => 1,
          "ingestion_cost_usd" => 0.01, "ingestion_seconds" => 0.5
        }
      },
      "queries_per_strategy" => {}
    }
  end

  test "persists and types the payload back" do
    comparison = Rag::ChunkingComparison.create!(
      strategies: [ "structural" ], queries: [], top_k: 3,
      corpus_label: "budgets_sample", corpus_count: 17,
      response_payload: minimal_payload, duration_ms: 1234
    )

    response = comparison.to_response
    assert_kind_of Rag::ComparisonResponse, response
    assert_equal 60, response.stats_for("structural").n_chunks
    assert_equal [ "Structural" ], comparison.strategy_labels
    assert_in_delta 0.01, comparison.cost_total_usd, 1e-9
    assert_equal 1.2, comparison.duration_seconds
  end

  test "requires at least one strategy and a sane top_k" do
    assert_not Rag::ChunkingComparison.new(strategies: [], top_k: 3).valid?
    assert_not Rag::ChunkingComparison.new(strategies: [ "structural" ], top_k: 0).valid?
    assert Rag::ChunkingComparison.new(strategies: [ "structural" ], top_k: 3).valid?
  end
end
