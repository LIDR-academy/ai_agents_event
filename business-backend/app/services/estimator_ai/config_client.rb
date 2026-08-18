# Context client for the runtime model configuration (Settings UI).
# Lets the instructor switch LLM models mid-session without .env round-trips.
module EstimatorAi
  class ConfigClient < BaseClient
    def get_models
      handle_response(json_conn.get("/api/v1/config/models"))
    end

    # ``changes`` is a partial hash: { "PRIMARY_MODEL" => "gpt-4o",
    # "CRITIC_MODEL" => nil } — nil resets that key to its .env default.
    def update_models(changes)
      raise ArgumentError, "changes must be a non-empty hash" if changes.blank?

      handle_response(json_conn.put("/api/v1/config/models", { models: changes }))
    end
  end
end
