require "test_helper"
require "webmock/minitest"

class RagChunkingComparisonsControllerTest < ActionDispatch::IntegrationTest
  setup do
    WebMock.disable_net_connect!
  end

  teardown do
    WebMock.reset!
    WebMock.allow_net_connect!
  end

  def compare_body
    {
      stats_per_strategy: {
        structural: {
          strategy: "structural", n_chunks: 60,
          token_distribution: { min: 40, p50: 70.0, p95: 120.0, max: 1391 },
          n_orphan_chunks: 0, n_obese_chunks: 1,
          ingestion_cost_usd: 0.0, ingestion_seconds: 0.0
        },
        hierarchical: {
          strategy: "hierarchical", n_chunks: 77,
          token_distribution: { min: 12, p50: 70.0, p95: 600.0, max: 1472 },
          n_orphan_chunks: 2, n_obese_chunks: 2,
          ingestion_cost_usd: 0.0, ingestion_seconds: 0.1
        }
      },
      queries_per_strategy: {
        structural: [
          { strategy: "structural", query: "GDPR compliance",
            top_k: [ { chunk_id: "BUD-2024-009::CONSENT-004", cosine: 0.418, text_preview: "consent…" } ] }
        ],
        hierarchical: [
          { strategy: "hierarchical", query: "GDPR compliance",
            top_k: [
              { chunk_id: "BUD-2024-009::CONSENT-004", cosine: 0.418, text_preview: "consent…" },
              { chunk_id: "BUD-2024-012::parent", cosine: 0.307, text_preview: "whole budget…" }
            ] }
        ]
      }
    }
  end

  def stub_compare(status: 200, body: compare_body)
    stub_request(:post, %r{/embeddings/compare})
      .to_return(status: status, body: body.to_json,
                 headers: { "Content-Type" => "application/json" })
  end

  test "new renders the lab form with the catalog" do
    get new_rag_chunking_comparison_path
    assert_response :success
    assert_select "input[type=checkbox][name='strategies[]']", count: 8
    assert_select "input[name='queries[]']"
  end

  test "create runs the comparison, persists it and redirects to show" do
    stub_compare

    assert_difference -> { Rag::ChunkingComparison.count }, 1 do
      post rag_chunking_comparisons_path, params: {
        strategies: %w[structural hierarchical],
        queries: [ "GDPR compliance", "" ],
        top_k: 3
      }
    end

    comparison = Rag::ChunkingComparison.order(:id).last
    assert_redirected_to rag_chunking_comparison_path(comparison)
    assert_equal %w[structural hierarchical], comparison.strategies
    assert_equal [ "GDPR compliance" ], comparison.queries  # blanks stripped
    assert_equal 17, comparison.corpus_count
    assert_not_nil comparison.duration_ms
  end

  test "create rejects unknown strategy names before calling the service" do
    stub = stub_compare

    post rag_chunking_comparisons_path, params: { strategies: %w[foo bar], top_k: 3 }

    assert_response :unprocessable_entity
    assert_not_requested stub
  end

  test "create with no strategies re-renders with a flash" do
    post rag_chunking_comparisons_path, params: { strategies: [], top_k: 3 }
    assert_response :unprocessable_entity
    assert_match "Selecciona al menos una estrategia", response.body
  end

  test "create surfaces a 400 unknown strategy from the service as a flash" do
    stub_compare(status: 400, body: { detail: "Unknown strategy: foo" })

    post rag_chunking_comparisons_path, params: { strategies: %w[structural], top_k: 3 }

    assert_response :unprocessable_entity
    assert_match "Unknown strategy: foo", response.body
  end

  test "create maps a missing-API-key 500 to an actionable flash" do
    stub_compare(status: 500, body: { detail: "PropositionalChunker requires OPENAI_API_KEY." })

    post rag_chunking_comparisons_path, params: { strategies: %w[propositional], top_k: 3 }

    assert_response :service_unavailable
    assert_match "API key", response.body
  end

  test "show renders stats, cost bars and the playground with parent/child badges" do
    stub_compare
    post rag_chunking_comparisons_path, params: {
      strategies: %w[structural hierarchical], queries: [ "GDPR compliance" ], top_k: 3
    }
    comparison = Rag::ChunkingComparison.order(:id).last

    get rag_chunking_comparison_path(comparison)

    assert_response :success
    assert_match "Estadísticas por estrategia", response.body
    assert_match "BUD-2024-012::parent", response.body
    assert_match(/>\s*parent\s*</, response.body)
    assert_match(/>\s*child\s*</, response.body)
    assert_match "0.4180", response.body
  end

  test "index lists persisted runs" do
    stub_compare
    post rag_chunking_comparisons_path, params: { strategies: %w[structural], top_k: 3 }

    get rag_chunking_comparisons_path

    assert_response :success
    assert_match "Structural", response.body
    assert_match "Ver →", response.body
  end
end
