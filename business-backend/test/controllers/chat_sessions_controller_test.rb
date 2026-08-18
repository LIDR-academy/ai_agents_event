require "test_helper"
require "webmock/minitest"

class ChatSessionsControllerTest < ActionDispatch::IntegrationTest
  setup do
    WebMock.disable_net_connect!
    @base_url = Rails.application.config.estimator_ai.base_url
    @valid_params = {
      conversation_request: {
        transcript:   "Quiero estimar un CRM llamado Nimbus en React + Postgres para el equipo de ventas.",
        project_type: "web_saas",
        detail_level: "medium",
        output_format: "phases_table"
      }
    }
  end

  teardown do
    WebMock.reset!
    WebMock.allow_net_connect!
  end

  def structured_body(prompt_version: "v2", cached: false)
    {
      result: {
        summary: "Nimbus CRM mid-size build with React + Postgres.",
        confidence_pct: 70,
        phases: [
          { name: "Discovery", duration_weeks: 1, cost_eur: 5_000, summary: "Workshops." },
          { name: "Build",     duration_weeks: 5, cost_eur: 20_000, summary: "Core build." }
        ],
        total_duration_weeks: 6,
        total_cost_eur: 25_000
      },
      prompt_version: prompt_version,
      cached: cached
    }
  end

  def session_info_body(metadata: nil, message_count: 2)
    {
      session_id: "remote-abc",
      message_count: message_count,
      max_turns: 6,
      metadata: metadata || {
        project_name: "Nimbus",
        assumed_team_size: 3,
        mentioned_technologies: [ "React", "Postgres" ],
        agreed_scope: "Phase 1 MVP CRM."
      }
    }
  end

  test "GET new creates a remote session lazily and renders the form" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })

    assert_difference -> { ChatSession.count }, 1 do
      get new_chat_session_path
    end
    assert_response :success
    assert_select "form"
    assert_select "input[type=file][name='attachments[]'][multiple]"
    assert_select "textarea[name='conversation_request[transcript]']"
  end

  test "POST create persists the estimation, refreshes metadata, and redirects" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_request(:post, "#{@base_url}/sessions/remote-abc/estimate")
      .to_return(status: 200, body: structured_body.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_request(:get, "#{@base_url}/sessions/remote-abc")
      .to_return(status: 200, body: session_info_body.to_json,
                 headers: { "Content-Type" => "application/json" })

    get new_chat_session_path
    chat_session = ChatSession.order(:created_at).last

    assert_difference -> { Estimation.count }, 1 do
      post chat_session_path(chat_session), params: @valid_params
    end

    assert_redirected_to chat_session_path(chat_session)
    estimation = Estimation.order(:created_at).last
    assert_equal "v2", estimation.prompt_version
    assert_equal chat_session.id, estimation.chat_session_id

    chat_session.reload
    assert_equal "Nimbus", chat_session.latest_metadata["project_name"]
    assert_equal 3, chat_session.latest_metadata["assumed_team_size"]
    assert_equal [ "React", "Postgres" ], chat_session.latest_metadata["mentioned_technologies"]
    assert_equal 1, chat_session.turn_count

    follow_redirect!
    assert_response :success
    assert_match "Nimbus", response.body
    assert_match "React", response.body
  end

  test "POST create with invalid params re-renders new with 422" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })

    get new_chat_session_path
    chat_session = ChatSession.order(:created_at).last

    bad = @valid_params.deep_dup
    bad[:conversation_request][:transcript] = "too short"

    assert_no_difference -> { Estimation.count } do
      post chat_session_path(chat_session), params: bad
    end
    assert_response :unprocessable_entity
  end

  test "POST create handles GuardrailViolation without persisting" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_request(:post, "#{@base_url}/sessions/remote-abc/estimate")
      .to_return(status: 400,
                 body: { detail: { reason: "prompt_injection", message: "ignore previous instructions" } }.to_json,
                 headers: { "Content-Type" => "application/json" })

    get new_chat_session_path
    chat_session = ChatSession.order(:created_at).last

    assert_no_difference -> { Estimation.count } do
      post chat_session_path(chat_session), params: @valid_params
    end
    assert_response :unprocessable_entity
    assert_match "prompt_injection", response.body
  end

  test "POST create resets the conversation when FastAPI lost the session" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_request(:post, "#{@base_url}/sessions/remote-abc/estimate")
      .to_return(status: 404, body: { detail: "session_not_found" }.to_json,
                 headers: { "Content-Type" => "application/json" })

    get new_chat_session_path
    chat_session = ChatSession.order(:created_at).last
    chat_session_id = chat_session.id

    post chat_session_path(chat_session), params: @valid_params
    assert_redirected_to new_chat_session_path
    assert_nil ChatSession.find_by(id: chat_session_id), "stale local mirror should be destroyed"
  end

  test "DELETE chat_session clears the session and redirects to new" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })

    get new_chat_session_path
    chat_session = ChatSession.order(:created_at).last

    delete chat_session_path(chat_session)
    assert_redirected_to new_chat_session_path
    follow_redirect!
    # The "reset" branch should NOT recreate the session under the same id;
    # a fresh POST /sessions is fired.
    assert_response :success
  end

  test "POST with mode=acb hits /estimate-acb and persists the trace" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })

    acb_payload = structured_body(prompt_version: "v3").merge(
      acb: {
        iterations: [ { iteration: 0, decision_after: "accept", critic_verdict: "accept",
                        critic_confidence: 90, issue_summary: [] } ],
        final_decision: "accept", iterations_run: 1
      }
    )
    stub_request(:post, "#{@base_url}/sessions/remote-abc/estimate-acb")
      .to_return(status: 200, body: acb_payload.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_request(:get, "#{@base_url}/sessions/remote-abc")
      .to_return(status: 200, body: session_info_body.to_json,
                 headers: { "Content-Type" => "application/json" })

    get new_chat_session_path
    chat_session = ChatSession.order(:created_at).last

    post chat_session_path(chat_session), params: @valid_params.merge(mode: "acb")
    estimation = Estimation.order(:created_at).last
    assert_equal "accept", estimation.response_payload.dig("acb", "final_decision")
    assert_redirected_to chat_session_path(chat_session)

    follow_redirect!
    assert_response :success
    assert_match "Actor-Critic-Boss trace", response.body
  end

  test "POST with explicit tier forwards it to FastAPI" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_request(:get, "#{@base_url}/sessions/remote-abc")
      .to_return(status: 200, body: session_info_body.to_json,
                 headers: { "Content-Type" => "application/json" })

    forwarded = stub_request(:post, "#{@base_url}/sessions/remote-abc/estimate")
      .with { |req| req.body.to_s.include?("tier=executive") }
      .to_return(status: 200, body: structured_body.to_json,
                 headers: { "Content-Type" => "application/json" })

    get new_chat_session_path
    chat_session = ChatSession.order(:created_at).last

    post chat_session_path(chat_session), params: @valid_params.merge(tier: "executive")
    assert_requested forwarded
  end

  # Root now serves the home dashboard; the conversational page lives at its
  # own route and is linked from the navbar and the dashboard card.
  test "new chat session route renders the conversational page" do
    stub_request(:post, "#{@base_url}/sessions")
      .to_return(status: 201, body: { session_id: "remote-abc" }.to_json,
                 headers: { "Content-Type" => "application/json" })
    get new_chat_session_path
    assert_response :success
    assert_select "h1", "Conversational estimation"
  end
end
