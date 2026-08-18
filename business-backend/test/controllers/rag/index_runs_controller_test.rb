require "test_helper"
require "webmock/minitest"

class RagIndexRunsControllerTest < ActionDispatch::IntegrationTest
  setup { WebMock.disable_net_connect! }
  teardown do
    WebMock.reset!
    WebMock.allow_net_connect!
  end

  def stats_body(total: 83)
    {
      total_chunks: total,
      collections: [
        { collection: "budget", documents: 10, chunks: 64, hnsw_indexed: true },
        { collection: "transcript", documents: 2, chunks: 11, hnsw_indexed: true },
        { collection: "technical_doc", documents: 1, chunks: 8, hnsw_indexed: false }
      ]
    }
  end

  def stub_stats(body: stats_body)
    stub_request(:get, %r{/embeddings/index/stats})
      .to_return(status: 200, body: body.to_json, headers: { "Content-Type" => "application/json" })
  end

  def budget_json
    {
      budget_id: "NEW-1", client_metadata: { name: "Acme", sector: "finance", country: "ES" },
      project_summary: "x", main_technology: "Python", year: 2025, total_estimated_hours: 100,
      components: [ { component_id: "C1", name: "c", description: "d", module: "Core",
                      tech_stack: [ "Python" ], estimated_hours: 100, complexity: "medium", dependencies: [] } ]
    }.to_json
  end

  test "index renders the corpus stats panel" do
    stub_stats
    get rag_index_runs_path
    assert_response :success
    assert_select "table"
  end

  test "new renders the form with the example JSON" do
    get new_rag_index_run_path
    assert_response :success
    assert_select "textarea[name=documents_json]"
  end

  test "create triggers the async job, persists the run and redirects to show" do
    stub_stats
    stub_request(:post, %r{/embeddings/index/runs})
      .to_return(status: 202,
                 body: { job_id: "abc-123", documents_total: 1, status: "pending" }.to_json,
                 headers: { "Content-Type" => "application/json" })

    assert_difference -> { Rag::IndexRun.count }, 1 do
      post rag_index_runs_path, params: { chunk_type: "budget_component", documents_json: budget_json }
    end
    run = Rag::IndexRun.order(:created_at).last
    assert_equal "abc-123", run.job_id
    assert_equal 1, run.submitted_count
    assert_redirected_to rag_index_run_path(run)
  end

  test "create rejects invalid JSON" do
    post rag_index_runs_path, params: { documents_json: "{ not json" }
    assert_response :unprocessable_entity
  end

  test "status polls the job and returns JSON" do
    run = Rag::IndexRun.create!(job_id: "job-9", chunk_type: "budget_component",
                                submitted_count: 2, status: "running",
                                before_stats: stats_body.deep_stringify_keys)
    stub_request(:get, %r{/embeddings/index/jobs/job-9})
      .to_return(status: 200,
                 body: { job_id: "job-9", status: "completed", documents_processed: 2,
                         error_message: nil, started_at: "2026-07-02T10:00:00Z", finished_at: nil }.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_stats(body: stats_body(total: 85))

    get status_rag_index_run_path(run)
    assert_response :success
    body = JSON.parse(response.body)
    assert_equal "completed", body["status"]
    assert body["finished"]
    assert_equal 2, body["documents_processed"]
  end
end
