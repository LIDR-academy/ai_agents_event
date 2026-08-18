require "test_helper"
require "webmock/minitest"

class EstimationsControllerTest < ActionDispatch::IntegrationTest
  setup do
    WebMock.disable_net_connect!
    @base_url = Rails.application.config.estimator_ai.base_url
    @valid_params = {
      estimation_request: {
        description: "Mobile app with login, chat and push notifications, multi-tenant",
        project_type: "mobile_app",
        detail_level: "medium",
        output_format: "phases_table"
      }
    }
  end

  teardown do
    WebMock.reset!
    WebMock.allow_net_connect!
  end

  def structured_body
    {
      result: {
        summary: "12-week estimation across 5 phases with QA included.",
        confidence_pct: 70,
        phases: [
          { name: "Discovery", duration_weeks: 1, cost_eur: 5_000, summary: "Scoping." },
          { name: "Build", duration_weeks: 6, cost_eur: 20_000, summary: "Core features." },
          { name: "QA",    duration_weeks: 1, cost_eur: 5_000, summary: "Test pass." }
        ],
        total_duration_weeks: 8,
        total_cost_eur: 30_000
      },
      prompt_version: "v1",
      cached: false
    }
  end

  test "GET new returns 200 with empty form" do
    get new_estimation_path
    assert_response :success
    assert_select "form"
    assert_select "textarea[name='estimation_request[description]']"
  end

  test "POST create with valid params persists and redirects to show" do
    stub_request(:post, "#{@base_url}/api/v1/estimate")
      .to_return(
        status: 200,
        body: structured_body.to_json,
        headers: { "Content-Type" => "application/json" }
      )

    assert_difference -> { Estimation.count }, 1 do
      post estimations_path, params: @valid_params
    end

    estimation = Estimation.order(:created_at).last
    assert_redirected_to estimation_path(estimation)
    assert_equal "v1", estimation.prompt_version
    assert_equal false, estimation.cached

    follow_redirect!
    assert_response :success
    assert_select "h1", "Estimation"
    assert_match "12-week estimation across 5 phases", response.body
    assert_match "Discovery", response.body
    assert_match "30,000", response.body
    assert_match "v1", response.body
  end

  test "GET show renders the persisted estimation" do
    payload = structured_body.merge(cached: true).deep_stringify_keys
    estimation = Estimation.create!(
      description:      @valid_params[:estimation_request][:description],
      project_type:     "mobile_app",
      detail_level:     "medium",
      output_format:    "phases_table",
      response_payload: payload,
      prompt_version:   "v1",
      cached:           true
    )

    get estimation_path(estimation)
    assert_response :success
    assert_match "12-week estimation across 5 phases", response.body
    assert_match "cached", response.body
  end

  test "GET index lists existing estimations" do
    Estimation.create!(
      description:      "Internal CRM with reporting",
      project_type:     "web_app",
      detail_level:     "high",
      output_format:    "phases_table",
      response_payload: structured_body.deep_stringify_keys,
      prompt_version:   "v1",
      cached:           false
    )

    get estimations_path
    assert_response :success
    assert_select "h1", "Estimaciones"
    assert_match "Internal CRM with reporting", response.body
  end

  test "POST create persists cached flag when upstream says cached" do
    stub_request(:post, "#{@base_url}/api/v1/estimate")
      .to_return(
        status: 200,
        body: structured_body.merge(cached: true).to_json,
        headers: { "Content-Type" => "application/json" }
      )

    post estimations_path, params: @valid_params
    estimation = Estimation.order(:created_at).last
    assert_equal true, estimation.cached

    follow_redirect!
    assert_match "cached", response.body
  end

  test "POST create with invalid params re-renders new with 422 and does not persist" do
    bad = @valid_params.deep_dup
    bad[:estimation_request][:description] = "too short"

    assert_no_difference -> { Estimation.count } do
      post estimations_path, params: bad
    end

    assert_response :unprocessable_entity
    assert_select "form"
  end

  test "POST create handles GuardrailViolation from upstream without persisting" do
    stub_request(:post, "#{@base_url}/api/v1/estimate")
      .to_return(
        status: 400,
        body: { detail: { reason: "prompt_injection", message: "ignore previous instructions" } }.to_json,
        headers: { "Content-Type" => "application/json" }
      )

    assert_no_difference -> { Estimation.count } do
      post estimations_path, params: @valid_params
    end

    assert_response :unprocessable_entity
    assert_match "prompt_injection", response.body
  end

  test "POST create handles ServerError from upstream without persisting" do
    stub_request(:post, "#{@base_url}/api/v1/estimate")
      .to_return(status: 502, body: { detail: "boom" }.to_json,
                 headers: { "Content-Type" => "application/json" })

    assert_no_difference -> { Estimation.count } do
      post estimations_path, params: @valid_params
    end

    assert_response :service_unavailable
    assert_match "AI service unavailable", response.body
  end

  test "POST create handles connection failure without persisting" do
    stub_request(:post, "#{@base_url}/api/v1/estimate").to_timeout

    assert_no_difference -> { Estimation.count } do
      post estimations_path, params: @valid_params
    end

    assert_response :service_unavailable
    assert_match "AI service unavailable", response.body
  end
end
