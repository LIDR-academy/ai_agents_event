require "test_helper"
require "webmock/minitest"

module EstimatorAi
  # Session 15 — the shared service token the business backend sends to the AI
  # service on every call, plus the 401 mapping that goes with it.
  class ServiceTokenTest < ActiveSupport::TestCase
    setup do
      # The whole EstimatorAi error taxonomy (Error, Unauthorized, …) is defined INSIDE
      # base_client.rb, so Zeitwerk cannot autoload ``EstimatorAi::Unauthorized`` by name:
      # it only exists once that file has been loaded. Referencing BaseClient here makes
      # this file pass in isolation instead of depending on some earlier test having
      # instantiated a client first.
      EstimatorAi::BaseClient

      WebMock.disable_net_connect!
      @original_token = Rails.application.config.estimator_ai.service_token
      Rails.application.config.estimator_ai.service_token = "test-service-token"

      @request = Estimation::Request.new(
        description: "Mobile app with login, chat and push notifications",
        project_type: "mobile_app",
        detail_level: "medium",
        output_format: "phases_table"
      )
    end

    teardown do
      Rails.application.config.estimator_ai.service_token = @original_token
      WebMock.reset!
      WebMock.allow_net_connect!
    end

    test "every client sends the service token, not just the RAG one" do
      # EstimationsClient predates the Session 9 auth and used to send no headers
      # at all. Since the AI service now enforces the token as middleware, a
      # client that omits it gets a 401 on an endpoint that used to be open.
      stub = stub_request(:post, "http://ai-test/api/v1/estimate")
        .with(headers: { "X-Service-Token" => "test-service-token" })
        .to_return(status: 200, body: { result: {}, prompt_version: "v1", cached: false }.to_json,
                   headers: { "Content-Type" => "application/json" })

      EstimatorAi::EstimationsClient.new(base_url: "http://ai-test").estimate(@request)

      assert_requested stub
    end

    test "the RAG client sends BOTH the service token and its API key" do
      # The two layers are independent: the token gets you past the app-wide
      # middleware, the API key satisfies the Session 9 router. Merging must not
      # drop either one.
      Rails.application.config.estimator_ai.estimate_api_key = "test-estimate-key"

      stub = stub_request(:post, %r{/v1/estimate/stages/reformulate})
        .with(headers: {
          "X-Service-Token" => "test-service-token",
          "X-API-Key" => "test-estimate-key"
        })
        .to_return(status: 200, body: {}.to_json,
                   headers: { "Content-Type" => "application/json" })

      EstimatorAi::RagEstimateClient.new.reformulate(transcript: "a transcript")

      assert_requested stub
    end

    test "no header is sent when the token is not configured" do
      # Running the AI service locally with the check disabled is a supported
      # setup; an empty header would be indistinguishable from a wrong one.
      Rails.application.config.estimator_ai.service_token = nil

      stub = stub_request(:post, "http://ai-test/api/v1/estimate")
        .with { |req| !req.headers.key?("X-Service-Token") }
        .to_return(status: 200, body: { result: {}, prompt_version: "v1", cached: false }.to_json,
                   headers: { "Content-Type" => "application/json" })

      EstimatorAi::EstimationsClient.new(base_url: "http://ai-test").estimate(@request)

      assert_requested stub
    end

    test "a 401 raises Unauthorized with an actionable message" do
      # Before Session 15 this fell through to the catch-all and surfaced as
      # "unexpected status 401", which pointed at nothing.
      stub_request(:post, "http://ai-test/api/v1/estimate")
        .to_return(
          status: 401,
          body: { detail: { reason: "invalid_service_token", message: "nope" } }.to_json,
          headers: { "Content-Type" => "application/json" }
        )

      err = assert_raises(EstimatorAi::Unauthorized) do
        EstimatorAi::EstimationsClient.new(base_url: "http://ai-test").estimate(@request)
      end

      assert_includes err.message, "invalid_service_token"
      assert_includes err.message, "AI_SERVICE_TOKEN"
    end

    test "multipart requests also carry the default headers" do
      # Regression: multipart_conn never applied @default_headers. It went
      # unnoticed while every multipart endpoint was unauthenticated.
      session_id = "11111111-1111-4111-8111-111111111111"
      stub = stub_request(:post, "http://ai-test/sessions/#{session_id}/estimate")
        .with(headers: { "X-Service-Token" => "test-service-token" })
        .to_return(status: 200, body: {}.to_json,
                   headers: { "Content-Type" => "application/json" })

      request = Conversation::Request.new(
        transcript: "We need a mobile app with login, chat and push notifications.",
        project_type: "mobile_app"
      )
      EstimatorAi::SessionsClient.new(base_url: "http://ai-test")
        .estimate_in_session(session_id, request)

      assert_requested stub
    end
  end
end
