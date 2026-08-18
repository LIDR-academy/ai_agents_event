require "test_helper"
require "webmock/minitest"

class AiSettingsControllerTest < ActionDispatch::IntegrationTest
  setup do
    WebMock.disable_net_connect!
  end

  teardown do
    WebMock.reset!
    WebMock.allow_net_connect!
  end

  def config_body(primary_effective: "gpt-4o-mini", overridden: false)
    keys = %w[
      PRIMARY_MODEL FALLBACK_MODEL CRITIC_MODEL METADATA_EXTRACTOR_MODEL
      COMPRESSION_MODEL PROPOSITIONAL_CHUNKER_MODEL CONTEXTUAL_CHUNKER_MODEL
    ]
    models = keys.index_with do |_key|
      { effective: "gpt-4o-mini", default: "gpt-4o-mini", overridden: false }
    end
    models["PRIMARY_MODEL"] = {
      effective: primary_effective, default: "gpt-4o-mini", overridden: overridden
    }
    {
      models: models,
      available_models: [ "gpt-4o-mini", "gpt-4o", "claude-sonnet-4-5" ],
      embedding_model: "text-embedding-3-small",
      embedding_model_note: "Read-only: changing it would invalidate all stored vectors."
    }
  end

  def stub_get(body: config_body)
    stub_request(:get, %r{/api/v1/config/models})
      .to_return(status: 200, body: body.to_json,
                 headers: { "Content-Type" => "application/json" })
  end

  test "show renders one row per knob plus the read-only embedding row" do
    stub_get

    get ai_settings_path

    assert_response :success
    assert_select "select[name='models[PRIMARY_MODEL]']"
    assert_select "select", count: 7
    assert_match "EMBEDDING_MODEL", response.body
    assert_match "read-only", response.body
  end

  test "show shows the override badge when a knob is overridden" do
    stub_get(body: config_body(primary_effective: "gpt-4o", overridden: true))

    get ai_settings_path

    assert_response :success
    assert_match(/>\s*override\s*</, response.body)
  end

  test "show degrades gracefully when the service is down" do
    stub_request(:get, %r{/api/v1/config/models}).to_timeout

    get ai_settings_path

    assert_response :service_unavailable
    assert_match "no está disponible", response.body
  end

  test "update PUTs the changes and redirects with a notice" do
    put_stub = stub_request(:put, %r{/api/v1/config/models})
      .with { |req| JSON.parse(req.body)["models"]["PRIMARY_MODEL"] == "gpt-4o" }
      .to_return(status: 200,
                 body: config_body(primary_effective: "gpt-4o", overridden: true).to_json,
                 headers: { "Content-Type" => "application/json" })

    put ai_settings_path, params: { models: { PRIMARY_MODEL: "gpt-4o" } }

    assert_redirected_to ai_settings_path
    assert_match "Modelos actualizados", flash[:notice]
    assert_requested put_stub
  end

  test "update sends empty selects as nil (reset to default)" do
    put_stub = stub_request(:put, %r{/api/v1/config/models})
      .with { |req| JSON.parse(req.body)["models"]["PRIMARY_MODEL"].nil? }
      .to_return(status: 200, body: config_body.to_json,
                 headers: { "Content-Type" => "application/json" })

    put ai_settings_path, params: { models: { PRIMARY_MODEL: "" } }

    assert_redirected_to ai_settings_path
    assert_requested put_stub
  end

  test "update surfaces a rejected change as a flash and re-renders" do
    stub_request(:put, %r{/api/v1/config/models})
      .to_return(status: 422, body: { detail: "Model 'foo' is not in the catalog" }.to_json,
                 headers: { "Content-Type" => "application/json" })
    stub_get

    put ai_settings_path, params: { models: { PRIMARY_MODEL: "gpt-4o" } }

    assert_response :unprocessable_entity
    assert_match "Cambio rechazado", response.body
  end
end
