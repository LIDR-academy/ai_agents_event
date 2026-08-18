module AiSettingsHelper
  # Effective primary model for the navbar badge. Cached briefly so the badge
  # costs at most one HTTP call every 15s; hidden (nil) when the service is
  # unreachable — a dead estimator must never block page rendering.
  def current_primary_model
    Rails.cache.fetch("ai:primary_model", expires_in: 15.seconds) do
      EstimatorAi::ConfigClient.new(timeout: 3)
        .get_models
        .dig("models", "PRIMARY_MODEL", "effective")
    end
  rescue StandardError
    # Pure decoration: any failure (service down, timeout, WebMock in tests)
    # hides the badge instead of breaking the page.
    nil
  end
end
