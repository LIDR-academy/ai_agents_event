# Catalog of the eight chunking strategies served by POST /embeddings/compare.
# Single source of truth for the lab form (labels, default selection), the
# cost warnings and the server-side validation of requested names.
#
# Mirrors ALL_STRATEGIES in the estimator's app/dependencies.py — same names,
# same canonical order.
module Rag
  class Strategy
    Entry = Struct.new(:name, :label, :description, :cost_tier, :needs_key, :default_checked,
                       keyword_init: true)

    CATALOG = [
      Entry.new(name: "structural", label: "Structural",
                description: "Un componente del presupuesto = un chunk, con cabecera de contexto.",
                cost_tier: :free, needs_key: nil, default_checked: true),
      Entry.new(name: "fixed_size", label: "Fixed size",
                description: "Ventanas fijas de 512 tokens con solape — el baseline degenerado.",
                cost_tier: :free, needs_key: nil, default_checked: true),
      Entry.new(name: "recursive", label: "Recursive",
                description: "Separadores jerárquicos (párrafo → línea → frase) — el default razonable.",
                cost_tier: :free, needs_key: nil, default_checked: true),
      Entry.new(name: "sentence_window", label: "Sentence window",
                description: "Frases individuales con ventana ±2 en metadata.",
                cost_tier: :free, needs_key: nil, default_checked: false),
      Entry.new(name: "hierarchical", label: "Hierarchical",
                description: "Dos niveles indexados: padres (presupuesto) e hijos (componentes).",
                cost_tier: :free, needs_key: nil, default_checked: false),
      Entry.new(name: "semantic", label: "Semantic",
                description: "Cortes donde cae la similitud entre frases (embeddings en ingesta).",
                cost_tier: :cheap, needs_key: :openai, default_checked: false),
      Entry.new(name: "propositional", label: "Propositional",
                description: "Un LLM descompone cada componente en proposiciones atómicas.",
                cost_tier: :expensive, needs_key: :openai, default_checked: false),
      Entry.new(name: "contextual_retrieval", label: "Contextual retrieval",
                description: "Claude enriquece cada chunk con contexto del documento padre (~$0.14, ~3 min).",
                cost_tier: :expensive, needs_key: :anthropic, default_checked: false)
    ].freeze

    ALL_NAMES = CATALOG.map(&:name).freeze

    def self.find(name)
      CATALOG.find { |entry| entry.name == name }
    end

    def self.defaults
      CATALOG.select(&:default_checked).map(&:name)
    end

    def self.expensive?(name)
      find(name)&.cost_tier == :expensive
    end

    def self.expensive_selected?(names)
      names.any? { |name| expensive?(name) }
    end

    def self.label_for(name)
      find(name)&.label || name
    end
  end
end
