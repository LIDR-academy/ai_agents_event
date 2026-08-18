require "test_helper"
require "webmock/minitest"

class RagEstimationRunsControllerTest < ActionDispatch::IntegrationTest
  STAGES = "/v1/estimate/stages".freeze

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

  def stub_reformulate(query: { "function" => "online store", "sector" => "ecommerce", "scale" => "medium" })
    stub_request(:post, %r{#{STAGES}/reformulate})
      .to_return(status: 200,
                 body: { query: query, search_text: "online store card checkout ecommerce" }.to_json,
                 headers: { "Content-Type" => "application/json" })
  end

  def run_with_reformulation
    stub_reformulate
    transcript = "x" * 150
    post rag_estimation_runs_path, params: { estimation_run: { transcript: transcript } }
    Rag::EstimationRun.order(:id).last
  end

  # --- new / create ----------------------------------------------------------

  test "new renders the transcript form" do
    get new_rag_estimation_run_path
    assert_response :success
    assert_select "textarea[name='estimation_run[transcript]']"
  end

  test "create persists the run, runs reformulation and sends the API key" do
    stub = stub_reformulate
    assert_difference -> { Rag::EstimationRun.count }, 1 do
      post rag_estimation_runs_path, params: { estimation_run: { transcript: "x" * 150 } }
    end
    run = Rag::EstimationRun.order(:id).last
    assert_redirected_to rag_estimation_run_path(run, step: "reformulation")
    assert_equal "ecommerce", run.reformulation_view.query.sector
    assert_equal "reformulated", run.status
    assert_requested(:post, %r{#{STAGES}/reformulate}, headers: { "X-API-Key" => "test-estimate-key" })
    assert_requested stub
  end

  test "create with a blank transcript re-renders" do
    post rag_estimation_runs_path, params: { estimation_run: { transcript: "" } }
    assert_response :unprocessable_entity
    assert_no_difference -> { Rag::EstimationRun.count } do
      post rag_estimation_runs_path, params: { estimation_run: { transcript: "  " } }
    end
  end

  # --- generate (S12 agent proposes the structure) ---------------------------

  def stub_structure(estimate:, agent_trace: nil)
    body = { estimate: estimate, fabricated_source_ids: [], coherent: true }
    body[:agent_trace] = agent_trace if agent_trace
    stub_request(:post, %r{/v1/estimate/agent/structure})
      .to_return(status: 200, body: body.to_json,
                 headers: { "Content-Type" => "application/json" })
  end

  test "generate has the agent propose a structure-only tree and routes to human review" do
    run = run_with_reformulation
    estimate = {
      "total_engineer_days" => nil, "confidence" => "high", "reasoning" => "decomposed from the brief",
      "modules" => [
        { "name" => "Auth", "tasks" => [ { "name" => "OAuth", "sources" => [] } ] },
        { "name" => "Checkout", "tasks" => [ { "name" => "Cart", "sources" => [] } ] }
      ],
      "sources" => [], "assumptions" => []
    }
    stub = stub_structure(
      estimate: estimate,
      agent_trace: { steps: [ { step: 1, tool: "propose_structure",
        tool_args: { modules: 2 }, observation: "2 modules / 2 tasks" } ] }
    )

    post generate_rag_estimation_run_path(run), params: {}
    run.reload
    # The wizard calls the AGENT structure endpoint, not the deterministic stage.
    assert_requested(:post, %r{/v1/estimate/agent/structure}, headers: { "X-API-Key" => "test-estimate-key" })
    assert_requested stub
    # The editable STRUCTURE is seeded (no hours yet); not the cost breakdown.
    assert_equal 2, run.structure_modules.size
    assert_not run.adjusted?
    assert_equal "generated", run.status
    # The agent's decomposition trace rode along in the generation JSONB.
    assert_equal "propose_structure", run.generation_agent_trace.steps.first.tool
    assert_redirected_to rag_estimation_run_path(run, step: "review")
  end

  # --- estimate_hours (deterministic per-task search + agent recovery) --------

  def stub_task_hours(tasks:, agent_trace: nil)
    body = { tasks: tasks }
    body[:agent_trace] = agent_trace if agent_trace
    stub_request(:post, %r{/v1/estimate/agent/hours})
      .to_return(status: 200, body: body.to_json,
                 headers: { "Content-Type" => "application/json" })
  end

  test "estimate_hours saves the reviewed structure and derives per-task hours" do
    run = Rag::EstimationRun.create!(transcript: "x" * 150,
      structure: { "modules" => [ { "name" => "Auth", "tasks" => [ { "name" => "OAuth" } ] } ] })

    stub = stub_task_hours(tasks: [
      { module: "Auth", task: "OAuth", estimated_hours: 40, reliability: 0.8, has_match: true,
        dispersion: 0.1, neighbors: [ { source_id: 1, budget_id: "b", estimated_hours: 40, distance: 0.1 } ] },
      { module: "Auth", task: "RBAC", has_match: false }
    ])

    patch_params = {
      modules: { "0" => { name: "Auth", description: "Access", tasks: {
        "0" => { name: "OAuth" },
        "1" => { name: "RBAC" }
      } } }
    }
    post estimate_hours_rag_estimation_run_path(run), params: patch_params
    run.reload

    assert_requested(:post, %r{/v1/estimate/agent/hours}, headers: { "X-API-Key" => "test-estimate-key" })
    assert_requested stub
    assert_equal 2, run.task_hours_view.total_count
    assert_equal 1, run.task_hours_view.flagged_count
    # The cost breakdown is seeded with the matched hours + a default rate.
    seeded_task = run.adjusted_modules.first.tasks.first
    assert_equal 40, seeded_task.estimated_hours
    assert seeded_task.rate_eur_per_hour.positive?
    assert run.adjusted_modules.first.tasks.last.flagged?
    assert_equal "hours_estimated", run.status
    assert_redirected_to rag_estimation_run_path(run, step: "hours")
  end

  # --- verify (cost confirmation) ---------------------------------------------

  test "verify recomputes the cost server-side and stores the confirmed estimate" do
    run = Rag::EstimationRun.create!(transcript: "x" * 150,
      task_hours: { "tasks" => [] },
      adjusted_breakdown: { "modules" => [], "total_hours" => 0, "total_cost_eur" => 0, "confirmed_at" => nil })

    # Nested, integer-indexed params (as the Stimulus editor serialises them).
    patch verify_rag_estimation_run_path(run), params: {
      modules: {
        "0" => { name: "Auth", description: "Access", tasks: {
          "0" => { name: "OAuth", estimated_hours: "40", rate_eur_per_hour: "80" },
          "1" => { name: "RBAC", estimated_hours: "10", rate_eur_per_hour: "60" },
          "2" => { name: "", estimated_hours: "99", rate_eur_per_hour: "99" } # blank task dropped
        } },
        "1" => { name: "", tasks: {} } # blank module dropped
      }
    }
    run.reload
    assert_equal "confirmed", run.status
    assert_equal 1, run.adjusted_modules.size
    assert_equal 2, run.adjusted_modules.first.tasks.size
    assert_equal 50, run.adjusted_total_hours # 40 + 10
    assert_equal 3800, run.adjusted_total_cost # 40*80 + 10*60
    assert run.confirmed?
  end

  # --- error mapping ---------------------------------------------------------

  test "a 502 from the agent structure phase surfaces as a flash on redirect" do
    run = run_with_reformulation
    stub_request(:post, %r{/v1/estimate/agent/structure})
      .to_return(status: 502, body: { detail: "Failed to propose the structure." }.to_json,
                 headers: { "Content-Type" => "application/json" })
    post generate_rag_estimation_run_path(run), params: {}
    assert_response :redirect
    follow_redirect!
    assert_match(/servicio IA/i, response.body)
  end

  # --- show renders every wizard step ----------------------------------------

  test "show renders generation, review, hours and verification screens" do
    run = Rag::EstimationRun.create!(
      transcript: "x" * 200,
      reformulation: { "query" => { "function" => "online store", "sector" => "ecommerce",
                                    "scale" => "medium", "technologies" => [ "Stripe" ] },
                       "search_text" => "online store stripe" },
      generation: { "estimate" => { "total_engineer_days" => nil, "duration_weeks" => nil,
                    "confidence" => "high", "reasoning" => "Decomposed from the brief",
                    "modules" => [ { "name" => "Auth", "description" => "Access", "tasks" => [
                      { "name" => "OAuth", "sources" => [] } ] } ],
                    "sources" => [], "assumptions" => [] },
                    "fabricated_source_ids" => [], "coherent" => true },
      structure: { "modules" => [ { "name" => "Auth", "description" => "Access",
                     "tasks" => [ { "name" => "OAuth", "sources" => [] } ] } ] },
      task_hours: { "tasks" => [ { "module" => "Auth", "task" => "OAuth", "estimated_hours" => 40,
                                   "reliability" => 0.8, "has_match" => true } ] },
      adjusted_breakdown: { "modules" => [ { "name" => "Auth", "tasks" => [
                              { "name" => "OAuth", "estimated_hours" => 40, "rate_eur_per_hour" => 75,
                                "hours_reliability" => 0.8, "has_match" => true, "sources" => [] } ] } ],
                            "total_hours" => 40, "total_cost_eur" => 3000, "confirmed_at" => nil }
    )

    get rag_estimation_run_path(run, step: "generation")
    assert_response :success
    assert_match "Confianza estructura", response.body
    assert_match "decomposición libre", response.body

    get rag_estimation_run_path(run, step: "review")
    assert_response :success
    assert_select "input[name='modules[0][tasks][0][name]']"
    assert_match "Estimar horas por tarea", response.body

    get rag_estimation_run_path(run, step: "hours")
    assert_response :success
    assert_match "fiab.", response.body

    get rag_estimation_run_path(run, step: "verification")
    assert_response :success
    assert_select "input[name='modules[0][tasks][0][estimated_hours]']"
    assert_select "input[name='modules[0][tasks][0][rate_eur_per_hour]']"
    assert_select "template[data-estimate-modules-editor-target='taskTemplate']"
  end

  test "show renders the grounding warning when citations are fabricated" do
    run = Rag::EstimationRun.create!(
      transcript: "x" * 200,
      generation: { "estimate" => { "confidence" => "high", "reasoning" => "r",
                    "modules" => [], "sources" => [], "assumptions" => [] },
                    "fabricated_source_ids" => [ 999 ], "coherent" => true }
    )
    get rag_estimation_run_path(run, step: "generation")
    assert_response :success
    assert_match "Citaciones inventadas", response.body
    assert_match "999", response.body
  end

  # --- agent profile forwarding (S12) ----------------------------------------

  test "generate runs with the fixed Neo persona (no agent selection)" do
    run = run_with_reformulation
    captured_body = nil
    stub_request(:post, %r{/v1/estimate/agent/structure})
      .with { |req| captured_body = JSON.parse(req.body); true }
      .to_return(status: 200, body: {
        estimate: { "confidence" => "high", "reasoning" => "r", "modules" => [],
                    "sources" => [], "assumptions" => [] },
        fabricated_source_ids: [], coherent: true
      }.to_json, headers: { "Content-Type" => "application/json" })

    # No profile_id: each step now has its own fixed agent.
    post generate_rag_estimation_run_path(run), params: {}

    assert captured_body["persona"].to_s.start_with?("You are Neo"), "structure step must run as Neo"
    # config is empty now → no model/effort overrides forwarded (service defaults win).
    assert_nil captured_body["model"]
    assert_nil captured_body["reasoning_effort"]
    assert captured_body.key?("query")
  end

  test "estimate_hours runs the recovery with the fixed Trinity persona" do
    run = run_with_reformulation
    run.update!(structure: { "modules" => [ { "name" => "Backend",
      "tasks" => [ { "name" => "API", "description" => "REST" } ] } ] }, status: "generated")
    captured_body = nil
    stub_request(:post, %r{/v1/estimate/agent/hours})
      .with { |req| captured_body = JSON.parse(req.body); true }
      .to_return(status: 200, body: { modules: [], total_engineer_days: 0 }.to_json,
                 headers: { "Content-Type" => "application/json" })

    post estimate_hours_rag_estimation_run_path(run),
         params: { modules: { "0" => { name: "Backend", tasks: { "0" => { name: "API" } } } } }

    assert captured_body["persona"].to_s.start_with?("You are Trinity"), "hours recovery must run as Trinity"
    assert_nil captured_body["model"]
  end

  test "estimate_hours surfaces a guardrail rejection as a flash" do
    run = Rag::EstimationRun.create!(transcript: "x" * 150,
      structure: { "modules" => [ { "name" => "Auth", "tasks" => [ { "name" => "OAuth" } ] } ] })
    stub_request(:post, %r{/v1/estimate/agent/hours})
      .to_return(status: 400,
                 body: { detail: { reason: "prompt_injection", message: "ignore instructions" } }.to_json,
                 headers: { "Content-Type" => "application/json" })
    post estimate_hours_rag_estimation_run_path(run), params: {
      modules: { "0" => { name: "Auth", tasks: { "0" => { name: "OAuth" } } } }
    }
    assert_redirected_to rag_estimation_run_path(run, step: run.current_step)
    assert_match(/guardarra/i, flash[:alert])
  end

  # --- index -----------------------------------------------------------------

  test "index lists persisted runs" do
    run_with_reformulation
    get rag_estimation_runs_path
    assert_response :success
    assert_match "Estimaciones guiadas", response.body
  end
end
