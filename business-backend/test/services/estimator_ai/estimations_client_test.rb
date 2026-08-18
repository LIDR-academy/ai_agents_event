require "test_helper"
require "webmock/minitest"

module EstimatorAi
  class EstimationsClientTest < ActiveSupport::TestCase
    setup do
      WebMock.disable_net_connect!
      @request = Estimation::Request.new(
        description: "Mobile app with login, chat and push notifications",
        project_type: "mobile_app",
        detail_level: "medium",
        output_format: "phases_table"
      )
      @client = EstimatorAi::EstimationsClient.new(base_url: "http://ai-test")
    end

    teardown do
      WebMock.reset!
      WebMock.allow_net_connect!
    end

    def structured_body
      {
        result: {
          summary: "Mid-size mobile app build.",
          confidence_pct: 70,
          phases: [
            { name: "Discovery", duration_weeks: 1, cost_eur: 5_000, summary: "Scoping." },
            { name: "Build", duration_weeks: 6, cost_eur: 20_000, summary: "Core features." }
          ],
          total_duration_weeks: 7,
          total_cost_eur: 25_000
        },
        prompt_version: "v1",
        cached: false
      }
    end

    test "returns raw Hash payload on 200 with structured body" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .with(body: @request.to_payload.to_json)
        .to_return(
          status: 200,
          body: structured_body.to_json,
          headers: { "Content-Type" => "application/json" }
        )

      payload = @client.estimate(@request)

      assert_kind_of Hash, payload
      assert_equal "v1", payload["prompt_version"]
      assert_equal false, payload["cached"]
      assert_equal 25_000, payload.dig("result", "total_cost_eur")
      assert_equal 2, payload.dig("result", "phases").size
    end

    test "cached flag is propagated from the API" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(
          status: 200,
          body: structured_body.merge(cached: true).to_json,
          headers: { "Content-Type" => "application/json" }
        )

      payload = @client.estimate(@request)
      assert_equal true, payload["cached"]
    end

    test "raises GuardrailViolation on 400 with prompt_injection reason" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(
          status: 400,
          body: {
            detail: { reason: "prompt_injection", message: "suspicious 'ignore previous instructions'" }
          }.to_json,
          headers: { "Content-Type" => "application/json" }
        )

      err = assert_raises(EstimatorAi::GuardrailViolation) do
        @client.estimate(@request)
      end
      assert_includes err.message, "prompt_injection"
    end

    test "raises GuardrailViolation on 400 with moderation reason" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(
          status: 400,
          body: { detail: { reason: "moderation", message: "flagged: hate" } }.to_json,
          headers: { "Content-Type" => "application/json" }
        )

      err = assert_raises(EstimatorAi::GuardrailViolation) { @client.estimate(@request) }
      assert_includes err.message, "moderation"
    end

    test "raises GuardrailViolation on 400 with pii reason" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(
          status: 400,
          body: { detail: { reason: "pii", message: "email detected" } }.to_json,
          headers: { "Content-Type" => "application/json" }
        )

      err = assert_raises(EstimatorAi::GuardrailViolation) { @client.estimate(@request) }
      assert_includes err.message, "pii"
    end

    test "falls back to InvalidRequest on 400 with unknown reason" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(
          status: 400,
          body: { detail: "something else went wrong" }.to_json,
          headers: { "Content-Type" => "application/json" }
        )

      assert_raises(EstimatorAi::InvalidRequest) { @client.estimate(@request) }
    end

    test "raises InvalidRequest on 422" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(
          status: 422,
          body: { detail: "description too short" }.to_json,
          headers: { "Content-Type" => "application/json" }
        )

      err = assert_raises(EstimatorAi::InvalidRequest) { @client.estimate(@request) }
      assert_includes err.message, "description too short"
    end

    test "raises ServerError on 502 with upstream LLM message" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(status: 502, body: { detail: "Upstream LLM call failed" }.to_json,
                   headers: { "Content-Type" => "application/json" })

      err = assert_raises(EstimatorAi::ServerError) { @client.estimate(@request) }
      assert_includes err.message, "Upstream LLM call failed"
    end

    test "raises ServerError on unexpected status" do
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(status: 500, body: "")

      assert_raises(EstimatorAi::ServerError) { @client.estimate(@request) }
    end

    test "raises ArgumentError when request is invalid" do
      bad = Estimation::Request.new
      assert_raises(ArgumentError) { @client.estimate(bad) }
    end
  end
end
