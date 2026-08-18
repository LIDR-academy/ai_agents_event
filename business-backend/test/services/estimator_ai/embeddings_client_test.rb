require "test_helper"
require "webmock/minitest"

module EstimatorAi
  class EmbeddingsClientTest < ActiveSupport::TestCase
    setup do
      WebMock.disable_net_connect!
      @client = EstimatorAi::EmbeddingsClient.new(base_url: "http://ai-test")
      @budgets = [ { "budget_id" => "BUD-2024-001" } ]
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
          }
        },
        queries_per_strategy: {
          structural: [
            {
              strategy: "structural", query: "OAuth authentication",
              top_k: [ { chunk_id: "BUD-2024-001::AUTH-001", cosine: 0.5684, text_preview: "…" } ]
            }
          ]
        }
      }
    end

    test "compare_chunking POSTs the full request body and returns the Hash on 200" do
      stub_request(:post, "http://ai-test/embeddings/compare")
        .with(body: {
          budgets: @budgets,
          queries: [ "OAuth authentication" ],
          strategies: [ "structural" ],
          top_k: 3
        }.to_json)
        .to_return(status: 200, body: compare_body.to_json,
                   headers: { "Content-Type" => "application/json" })

      payload = @client.compare_chunking(
        budgets: @budgets, queries: [ "OAuth authentication" ],
        strategies: [ "structural" ], top_k: 3
      )

      assert_equal 60, payload.dig("stats_per_strategy", "structural", "n_chunks")
      assert_equal 0.5684,
        payload.dig("queries_per_strategy", "structural", 0, "top_k", 0, "cosine")
    end

    test "raises InvalidRequest on 400 unknown strategy" do
      stub_request(:post, "http://ai-test/embeddings/compare")
        .to_return(status: 400, body: { detail: "Unknown strategy: foo" }.to_json,
                   headers: { "Content-Type" => "application/json" })

      err = assert_raises(EstimatorAi::InvalidRequest) do
        @client.compare_chunking(budgets: @budgets, strategies: [ "foo" ])
      end
      assert_includes err.message, "Unknown strategy: foo"
    end

    test "raises ServerError on 500 missing API key" do
      stub_request(:post, "http://ai-test/embeddings/compare")
        .to_return(status: 500,
                   body: { detail: "PropositionalChunker requires OPENAI_API_KEY." }.to_json,
                   headers: { "Content-Type" => "application/json" })

      assert_raises(EstimatorAi::ServerError) do
        @client.compare_chunking(budgets: @budgets, strategies: [ "propositional" ])
      end
    end

    test "raises ArgumentError on empty budgets without touching the network" do
      assert_raises(ArgumentError) { @client.compare_chunking(budgets: []) }
    end
  end
end
