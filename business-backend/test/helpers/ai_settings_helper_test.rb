require "test_helper"
require "webmock/minitest"

class AiSettingsHelperTest < ActionView::TestCase
  include AiSettingsHelper

  setup do
    WebMock.disable_net_connect!
    # Swap in a real memory store: the test env default (null store) would
    # re-execute the fetch block on every call and hide caching bugs.
    @original_cache = Rails.cache
    Rails.cache = ActiveSupport::Cache::MemoryStore.new
  end

  teardown do
    Rails.cache = @original_cache
    WebMock.reset!
    WebMock.allow_net_connect!
  end

  test "returns the effective primary model and caches it" do
    stub = stub_request(:get, %r{/api/v1/config/models})
      .to_return(
        status: 200,
        body: { models: { PRIMARY_MODEL: { effective: "gpt-4o", default: "gpt-4o-mini",
                                           overridden: true } } }.to_json,
        headers: { "Content-Type" => "application/json" }
      )

    assert_equal "gpt-4o", current_primary_model
    assert_equal "gpt-4o", current_primary_model  # second read served from cache
    assert_requested stub, times: 1
  end

  test "returns nil when the service is unreachable (badge hidden)" do
    stub_request(:get, %r{/api/v1/config/models}).to_timeout

    assert_nil current_primary_model
  end
end
