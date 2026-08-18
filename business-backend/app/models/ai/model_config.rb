# Mirror of one entry of the FastAPI runtime-config snapshot
# (GET /api/v1/config/models → models[KEY]): which model is effective for a
# knob, what its .env default is, and whether a runtime override is active.
module Ai
  class ModelConfig
    include ActiveModel::Model
    include ActiveModel::Attributes

    # Display catalog for the seven runtime-configurable knobs, in UI order.
    Entry = Struct.new(:key, :label, :description, keyword_init: true)
    CATALOG = [
      Entry.new(key: "PRIMARY_MODEL", label: "Modelo primario",
                description: "El modelo de todas las estimaciones (transaccional, conversación y ACB actor)."),
      Entry.new(key: "FALLBACK_MODEL", label: "Modelo de fallback",
                description: "Respaldo del Router cuando el primario falla (solo sin override activo)."),
      Entry.new(key: "CRITIC_MODEL", label: "Critic (ACB)",
                description: "Auditor read-only del bucle Actor-Critic-Boss."),
      Entry.new(key: "METADATA_EXTRACTOR_MODEL", label: "Extractor de metadata",
                description: "Extrae project_name/tecnologías/scope una vez por turno conversacional."),
      Entry.new(key: "COMPRESSION_MODEL", label: "Compresión de historial",
                description: "Resumidor acumulativo de la memoria conversacional."),
      Entry.new(key: "PROPOSITIONAL_CHUNKER_MODEL", label: "Chunker propositional",
                description: "Descompone componentes en proposiciones atómicas (RAG Lab)."),
      Entry.new(key: "CONTEXTUAL_CHUNKER_MODEL", label: "Chunker contextual",
                description: "Claude enriquece cada chunk con contexto del presupuesto padre (RAG Lab).")
    ].freeze

    KEYS = CATALOG.map(&:key).freeze

    attribute :key, :string
    attribute :effective, :string
    attribute :default, :string
    attribute :overridden, :boolean, default: false

    def self.from_hash(key, raw)
      new(
        key: key,
        effective: raw["effective"],
        default: raw["default"],
        overridden: raw["overridden"] || false
      )
    end

    def entry
      CATALOG.find { |candidate| candidate.key == key }
    end

    def label = entry&.label || key
    def description = entry&.description
  end
end
