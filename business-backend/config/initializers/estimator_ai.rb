Rails.application.config.estimator_ai = ActiveSupport::OrderedOptions.new.tap do |c|
  c.base_url = ENV.fetch("ESTIMATOR_API_BASE_URL", "http://localhost:8000")
  c.timeout  = ENV.fetch("ESTIMATOR_AI_TIMEOUT", "180").to_i

  # Session 9 RAG endpoints (X-API-Key header). Must match the FastAPI service's
  # RETRIEVAL_API_KEY / ESTIMATE_API_KEY. Blank disables the RAG wizard (401).
  c.retrieval_api_key = ENV.fetch("RETRIEVAL_API_KEY", nil)
  c.estimate_api_key  = ENV.fetch("ESTIMATE_API_KEY", nil)

  # Session 15: shared service token (X-Service-Token header). Must match the
  # FastAPI service's AI_SERVICE_TOKEN. Unlike the keys above this one is sent
  # on EVERY request, because on the FastAPI side it is enforced as middleware
  # across the whole app rather than per router.
  #
  # Blank is valid and means "the AI service has the check disabled" — the
  # normal state when running it locally outside Docker.
  c.service_token = ENV.fetch("AI_SERVICE_TOKEN", nil)
end
