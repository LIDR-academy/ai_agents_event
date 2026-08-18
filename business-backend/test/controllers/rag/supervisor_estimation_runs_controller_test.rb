require "test_helper"
require "webmock/minitest"

# Session 14 — the supervisor flow and its review inbox. Blocking verbs (no streaming),
# so the controller's job is small and testable: start a run, persist whatever the
# service IA returned, render the right screen for that state, and feed the reviewer's
# decision back. WebMock stands in for FastAPI.
class RagSupervisorEstimationRunsControllerTest < ActionDispatch::IntegrationTest
  SUP = "/v1/estimate/supervisor".freeze

  setup do
    WebMock.disable_net_connect!
    @previous_key = Rails.application.config.estimator_ai.estimate_api_key
    Rails.application.config.estimator_ai.estimate_api_key = "test-estimate-key"
  end

  teardown do
    Rails.application.config.estimator_ai.estimate_api_key = @previous_key
    WebMock.reset!
    WebMock.allow_net_connect!
  end

  # --- fixtures: SupervisorRunState payloads -------------------------------- #
  def routing
    [
      { step: 0, next_agent: "requirements_extractor", reason: "only a transcript so far", source: "llm" },
      { step: 1, next_agent: "budget_searcher", reason: "components need references", source: "llm" },
      { step: 2, next_agent: "estimate_generator", reason: "references gathered", source: "fallback" }
    ]
  end

  def contributions
    [
      { step: 0, agent: "requirements_extractor", action: "extract_requirements",
        tool: nil, outcome: "ok", summary: "4 requirements", duration_ms: 120 },
      { step: 1, agent: "budget_searcher", action: "tool:validate_estimate",
        tool: "validate_estimate", outcome: "denied",
        summary: "agent 'budget_searcher' attempted tool 'validate_estimate'", duration_ms: 0 }
    ]
  end

  def paused_payload
    {
      estimation_id: "irrelevant", state: "paused", status: "awaiting_human_review",
      pending_review: {
        gate: "low_confidence_review", estimation_id: "irrelevant",
        reasons: [ "confidence 0.21 is below the 0.60 threshold",
                   "only 0/3 components have any precedent in the historical budgets" ],
        confidence: 0.21, threshold: 0.6,
        estimate: { components: [ { name: "Telemetría", engineer_days: 40, rationale: "guessed" } ],
                    total_engineer_days: 40 },
        validation: { ok: false, issues: [ "'Telemetría' has no historical reference (unbudgeted)." ] }
      },
      estimate: { components: [ { name: "Telemetría", engineer_days: 40, rationale: "guessed" } ],
                  total_engineer_days: 40 },
      confidence: 0.21,
      requirements: [ "telemetry ingestion" ], components: [ { name: "Telemetría", category: "backend" } ],
      budget_matches: [], validation: {}, human_decision: nil,
      routing_history: routing, agent_contributions: contributions,
      privilege_violations: [ contributions.last ], errors: [ "'Telemetría' has no historical reference (unbudgeted)." ]
    }
  end

  def completed_payload(status: "validated", decision: "approve")
    paused_payload.merge(
      state: "completed", status: status, pending_review: nil,
      human_decision: { decision: decision, note: "revisado" }
    )
  end

  def json(body, status: 200)
    { status: status, body: body.to_json, headers: { "Content-Type" => "application/json" } }
  end

  def paused_run
    Rag::SupervisorEstimationRun.create!(
      transcript: "x" * 150, estimation_id: SecureRandom.uuid,
      run_state: "paused", status: "awaiting_human_review",
      pending_review: paused_payload[:pending_review].deep_stringify_keys,
      estimate: paused_payload[:estimate].deep_stringify_keys,
      confidence: 0.21,
      routing_history: routing.map(&:deep_stringify_keys),
      agent_contributions: contributions.map(&:deep_stringify_keys)
    )
  end

  # --- new / index ----------------------------------------------------------- #
  test "new renders the form" do
    get new_rag_supervisor_estimation_run_path
    assert_response :success
    assert_select "textarea"
  end

  test "index separates the review inbox from the rest" do
    run = paused_run
    get rag_supervisor_estimation_runs_path
    assert_response :success
    assert_select "a", text: "##{run.id}"
    assert_match "Esperando revisión", response.body
  end

  # --- create ---------------------------------------------------------------- #
  test "create starts the run and persists a completed result" do
    stub_request(:post, %r{#{SUP}\z}).to_return(json(completed_payload))

    assert_difference "Rag::SupervisorEstimationRun.count", 1 do
      post rag_supervisor_estimation_runs_path,
           params: { supervisor_estimation_run: { transcript: "x" * 150 } }
    end

    run = Rag::SupervisorEstimationRun.order(:created_at).last
    assert_redirected_to rag_supervisor_estimation_run_path(run)
    assert run.completed?
    assert_equal "validated", run.status
    assert_equal 40, run.total_engineer_days
    assert_equal 3, run.routing_history.size
  end

  test "create sends the estimate API key" do
    stub_request(:post, %r{#{SUP}\z}).to_return(json(completed_payload))
    post rag_supervisor_estimation_runs_path,
         params: { supervisor_estimation_run: { transcript: "x" * 150 } }
    assert_requested(:post, %r{#{SUP}\z}) { |req| req.headers["X-Api-Key"] == "test-estimate-key" }
  end

  test "create persists a paused run into the inbox" do
    stub_request(:post, %r{#{SUP}\z}).to_return(json(paused_payload))
    post rag_supervisor_estimation_runs_path,
         params: { supervisor_estimation_run: { transcript: "x" * 150 } }

    run = Rag::SupervisorEstimationRun.order(:created_at).last
    assert run.paused?
    assert run.awaiting_review?
    assert_equal 2, run.review_reasons.size
    assert_equal 0.6, run.threshold
    assert_includes Rag::SupervisorEstimationRun.awaiting_review, run
  end

  test "create keeps the row when the service rejects the input" do
    stub_request(:post, %r{#{SUP}\z}).to_return(
      json({ detail: { reason: "prompt_injection", message: "nope" } }, status: 400)
    )

    assert_difference "Rag::SupervisorEstimationRun.count", 1 do
      post rag_supervisor_estimation_runs_path,
           params: { supervisor_estimation_run: { transcript: "x" * 150 } }
    end
    assert_match(/guardarra/i, flash[:alert])
  end

  test "create rerenders when the transcript is blank" do
    assert_no_difference "Rag::SupervisorEstimationRun.count" do
      post rag_supervisor_estimation_runs_path,
           params: { supervisor_estimation_run: { transcript: "" } }
    end
    assert_response :unprocessable_entity
  end

  # --- show ------------------------------------------------------------------ #
  test "show renders the review screen with the reasons and the traces" do
    run = paused_run
    get rag_supervisor_estimation_run_path(run)
    assert_response :success
    assert_match "necesita revisión humana", response.body
    assert_match "below the 0.60 threshold", response.body
    # The routing trace and the denied action are both surfaced.
    assert_match "Enrutado del supervisor", response.body
    assert_match "fallback", response.body
    assert_match "DENEGADA", response.body
  end

  test "show renders the result screen once completed" do
    run = paused_run
    run.update!(run_state: "completed", status: "validated",
                human_decision: { "decision" => "approve", "note" => "revisado" })
    get rag_supervisor_estimation_run_path(run)
    assert_response :success
    assert_match "Decisión humana", response.body
    assert_no_match(/necesita revisión humana/, response.body)
  end

  # --- resume ---------------------------------------------------------------- #
  test "resume approves and persists the completed state" do
    run = paused_run
    stub_request(:post, %r{#{SUP}/#{run.estimation_id}/resume\z}).to_return(json(completed_payload))

    post resume_rag_supervisor_estimation_run_path(run), params: { decision: "approve", note: "revisado" }

    assert_redirected_to rag_supervisor_estimation_run_path(run)
    run.reload
    assert run.completed?
    assert_equal "validated", run.status
    assert_requested(:post, %r{/resume\z}) do |req|
      body = JSON.parse(req.body)
      body["decision"] == "approve" && body["note"] == "revisado" && !body.key?("estimate_overrides")
    end
  end

  test "resume with adjust sends the edited component days" do
    run = paused_run
    stub_request(:post, %r{#{SUP}/#{run.estimation_id}/resume\z}).to_return(json(completed_payload))

    post resume_rag_supervisor_estimation_run_path(run),
         params: { decision: "adjust", components: { "0" => { engineer_days: "65" } } }

    assert_requested(:post, %r{/resume\z}) do |req|
      body = JSON.parse(req.body)
      body["decision"] == "adjust" &&
        body.dig("estimate_overrides", "components", 0, "engineer_days") == 65
    end
  end

  test "resume with reject persists the rejected status" do
    run = paused_run
    stub_request(:post, %r{#{SUP}/#{run.estimation_id}/resume\z})
      .to_return(json(completed_payload(status: "rejected", decision: "reject")))

    post resume_rag_supervisor_estimation_run_path(run), params: { decision: "reject" }

    assert_equal "rejected", run.reload.status
  end

  test "resume surfaces a 409 as a flash rather than a crash" do
    run = paused_run
    stub_request(:post, %r{#{SUP}/#{run.estimation_id}/resume\z})
      .to_return(json({ detail: "No pending human review" }, status: 409))

    post resume_rag_supervisor_estimation_run_path(run), params: { decision: "approve" }

    assert_redirected_to rag_supervisor_estimation_run_path(run)
    assert flash[:alert].present?
  end

  test "an unknown decision falls back to approve rather than being forwarded" do
    run = paused_run
    stub_request(:post, %r{#{SUP}/#{run.estimation_id}/resume\z}).to_return(json(completed_payload))

    post resume_rag_supervisor_estimation_run_path(run), params: { decision: "sudo-approve" }

    assert_requested(:post, %r{/resume\z}) { |req| JSON.parse(req.body)["decision"] == "approve" }
  end
end
