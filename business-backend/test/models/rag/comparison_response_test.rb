require "test_helper"

class RagComparisonResponseTest < ActiveSupport::TestCase
  def sample_hash
    {
      "stats_per_strategy" => {
        "hierarchical" => {
          "strategy" => "hierarchical", "n_chunks" => 77,
          "token_distribution" => { "min" => 12, "p50" => 70.0, "p95" => 600.0, "max" => 1472 },
          "n_orphan_chunks" => 2, "n_obese_chunks" => 2,
          "ingestion_cost_usd" => 0.0, "ingestion_seconds" => 0.1
        },
        "structural" => {
          "strategy" => "structural", "n_chunks" => 60,
          "token_distribution" => { "min" => 40, "p50" => 70.0, "p95" => 120.0, "max" => 1391 },
          "n_orphan_chunks" => 0, "n_obese_chunks" => 1,
          "ingestion_cost_usd" => 0.14, "ingestion_seconds" => 202.7
        }
      },
      "queries_per_strategy" => {
        "hierarchical" => [
          {
            "strategy" => "hierarchical", "query" => "GDPR compliance",
            "top_k" => [
              { "chunk_id" => "BUD-2024-009::CONSENT-004", "cosine" => 0.418, "text_preview" => "child…" },
              { "chunk_id" => "BUD-2024-012::parent", "cosine" => 0.307, "text_preview" => "parent…" }
            ]
          }
        ],
        "structural" => [
          {
            "strategy" => "structural", "query" => "GDPR compliance",
            "top_k" => [
              { "chunk_id" => "BUD-2024-009::CONSENT-004", "cosine" => 0.418, "text_preview" => "…" }
            ]
          }
        ]
      }
    }
  end

  test "from_hash builds the nested typed tree" do
    response = Rag::ComparisonResponse.from_hash(sample_hash)

    stats = response.stats_for("structural")
    assert_kind_of Rag::Stats, stats
    assert_equal 60, stats.n_chunks
    assert_kind_of Rag::TokenDistribution, stats.token_distribution
    assert_equal 1391, stats.token_distribution.max
    assert stats.obese?
    assert_not stats.orphans?

    results = response.query_results_for("hierarchical")
    assert_kind_of Rag::QueryResult, results.first
    assert_kind_of Rag::TopChunk, results.first.top_k.first
  end

  test "strategy_names follows the canonical catalog order" do
    response = Rag::ComparisonResponse.from_hash(sample_hash)
    # structural comes before hierarchical in Rag::Strategy::CATALOG.
    assert_equal %w[structural hierarchical], response.strategy_names
  end

  test "queries and results_by_strategy_for group the playground data" do
    response = Rag::ComparisonResponse.from_hash(sample_hash)

    assert_equal [ "GDPR compliance" ], response.queries
    pairs = response.results_by_strategy_for("GDPR compliance")
    assert_equal %w[structural hierarchical], pairs.map(&:first)
    assert response.any_queries?
  end

  test "cost aggregates" do
    response = Rag::ComparisonResponse.from_hash(sample_hash)
    assert_in_delta 0.14, response.total_ingestion_cost_usd, 1e-9
    assert_in_delta 0.14, response.max_ingestion_cost_usd, 1e-9
  end

  test "TopChunk derives level and parent_label from the chunk_id" do
    parent = Rag::TopChunk.new(chunk_id: "BUD-2024-012::parent", cosine: 0.307, text_preview: "p")
    child  = Rag::TopChunk.new(chunk_id: "BUD-2024-009::CONSENT-004", cosine: 0.418, text_preview: "c")
    flat   = Rag::TopChunk.new(chunk_id: "chunk-7", cosine: 0.5, text_preview: "f")

    assert_equal :parent, parent.level
    assert_equal :child, child.level
    assert_nil flat.level
    assert_equal "BUD-2024-012", parent.parent_label
    assert_equal 42, child.cosine_pct
  end
end
