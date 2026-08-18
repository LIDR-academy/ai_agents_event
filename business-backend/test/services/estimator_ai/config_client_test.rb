require "test_helper"
require "webmock/minitest"

module EstimatorAi
  class ConfigClientTest < ActiveSupport::TestCase
    setup do
      WebMock.disable_net_connect!
      @client = EstimatorAi::ConfigClient.new(base_url: "http://ai-test")
    end

    teardown do
      WebMock.reset!
      WebMock.allow_net_connect!
    end

    def config_body(primary_effective: "gpt-4o-mini", overridden: false)
      {
        models: {
          PRIMARY_MODEL: { effective: primary_effective, default: "gpt-4o-mini", overridden: overridden },
          FALLBACK_MODEL: { effective: "claude-haiku-4-5-20251001", default: "claude-haiku-4-5-20251001", overridden: false }
        },
        available_models: [ "gpt-4o-mini", "gpt-4o" ],
        embedding_model: "text-embedding-3-small",
        embedding_model_note: "Read-only."
      }
    end

    test "get_models GETs the config endpoint and returns the Hash" do
      stub_request(:get, "http://ai-test/api/v1/config/models")
        .to_return(status: 200, body: config_body.to_json,
                   headers: { "Content-Type" => "application/json" })

      body = @client.get_models
      assert_equal "gpt-4o-mini", body.dig("models", "PRIMARY_MODEL", "effective")
      assert_includes body["available_models"], "gpt-4o"
    end

    test "update_models PUTs the partial changes and returns the fresh snapshot" do
      stub_request(:put, "http://ai-test/api/v1/config/models")
        .with(body: { models: { "PRIMARY_MODEL" => "gpt-4o" } }.to_json)
        .to_return(status: 200,
                   body: config_body(primary_effective: "gpt-4o", overridden: true).to_json,
                   headers: { "Content-Type" => "application/json" })

      body = @client.update_models("PRIMARY_MODEL" => "gpt-4o")
      assert_equal true, body.dig("models", "PRIMARY_MODEL", "overridden")
    end

    test "update_models serializes nil as a reset" do
      stub_request(:put, "http://ai-test/api/v1/config/models")
        .with(body: { models: { "PRIMARY_MODEL" => nil } }.to_json)
        .to_return(status: 200, body: config_body.to_json,
                   headers: { "Content-Type" => "application/json" })

      body = @client.update_models("PRIMARY_MODEL" => nil)
      assert_equal false, body.dig("models", "PRIMARY_MODEL", "overridden")
    end

    test "raises InvalidRequest on 422 unknown key" do
      stub_request(:put, "http://ai-test/api/v1/config/models")
        .to_return(status: 422, body: { detail: "Unknown model key: FOO" }.to_json,
                   headers: { "Content-Type" => "application/json" })

      err = assert_raises(EstimatorAi::InvalidRequest) do
        @client.update_models("FOO" => "gpt-4o")
      end
      assert_includes err.message, "Unknown model key"
    end

    test "raises InvalidRequest on 400 missing provider key" do
      stub_request(:put, "http://ai-test/api/v1/config/models")
        .to_return(status: 400,
                   body: { detail: "Model 'claude-sonnet-4-5' requires ANTHROPIC_API_KEY, which is not configured" }.to_json,
                   headers: { "Content-Type" => "application/json" })

      err = assert_raises(EstimatorAi::InvalidRequest) do
        @client.update_models("PRIMARY_MODEL" => "claude-sonnet-4-5")
      end
      assert_includes err.message, "ANTHROPIC_API_KEY"
    end

    test "raises ServerError on 503 store unavailable" do
      stub_request(:put, "http://ai-test/api/v1/config/models")
        .to_return(status: 503, body: { detail: "Runtime config store unavailable" }.to_json,
                   headers: { "Content-Type" => "application/json" })

      assert_raises(EstimatorAi::ServerError) { @client.update_models("PRIMARY_MODEL" => "gpt-4o") }
    end

    test "raises ArgumentError on empty changes without touching the network" do
      assert_raises(ArgumentError) { @client.update_models({}) }
    end
  end
end
