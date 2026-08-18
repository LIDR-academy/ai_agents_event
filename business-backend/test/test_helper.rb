ENV["RAILS_ENV"] ||= "test"
require_relative "../config/environment"
require "rails/test_help"
require "webmock/minitest"

module ActiveSupport
  class TestCase
    # Run tests in parallel with specified workers
    parallelize(workers: :number_of_processors)

    # Setup all fixtures in test/fixtures/*.yml for all tests in alphabetical order.
    fixtures :all

    # Add more helper methods to be used by all tests here...
  end
end

module ActionDispatch
  class IntegrationTest
    # The layout renders the primary-model badge on every page, which GETs
    # /api/v1/config/models through the AiSettingsHelper. Register a default
    # stub so every integration test renders without hitting the network;
    # tests that care about the config endpoint register their own (more
    # recent) stubs, which take precedence in WebMock.
    setup do
      stub_request(:get, %r{/api/v1/config/models})
        .to_return(
          status: 200,
          body: {
            models: {
              "PRIMARY_MODEL" => {
                "effective" => "gpt-4o-mini", "default" => "gpt-4o-mini", "overridden" => false
              }
            }
          }.to_json,
          headers: { "Content-Type" => "application/json" }
        )
    end
  end
end
