# Mirror of the full GET /api/v1/config/models response: the seven model
# knobs (in UI order), the selectable catalog (already filtered by the API
# keys configured on the service) and the read-only embedding model.
module Ai
  class Catalog
    attr_reader :model_configs, :available_models, :embedding_model, :embedding_model_note

    def self.from_hash(hash)
      models = hash["models"].to_h
      configs = Ai::ModelConfig::KEYS.filter_map do |key|
        Ai::ModelConfig.from_hash(key, models[key]) if models.key?(key)
      end
      new(
        model_configs: configs,
        available_models: Array(hash["available_models"]),
        embedding_model: hash["embedding_model"],
        embedding_model_note: hash["embedding_model_note"]
      )
    end

    def initialize(model_configs:, available_models:, embedding_model:, embedding_model_note: nil)
      @model_configs = model_configs
      @available_models = available_models
      @embedding_model = embedding_model
      @embedding_model_note = embedding_model_note
    end

    def primary_model
      model_configs.find { |config| config.key == "PRIMARY_MODEL" }&.effective
    end

    def any_overridden?
      model_configs.any?(&:overridden)
    end
  end
end
