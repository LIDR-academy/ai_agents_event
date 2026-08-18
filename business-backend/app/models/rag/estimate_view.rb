# Mirror of the FastAPI ``Estimate`` schema (S09, engineer-days + citations).
# Distinct from Estimation::Result (the S04 euros/weeks/phases contract).
module Rag
  class EstimateView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :total_engineer_days, :integer
    attribute :duration_weeks, :integer
    attribute :confidence, :string   # high | medium | low | insufficient
    attribute :reasoning, :string
    attribute :insufficient_context_explanation, :string

    attr_reader :modules, :sources, :assumptions

    def self.from_hash(hash)
      new(hash || {})
    end

    def initialize(attributes = {})
      stringified = (attributes || {}).transform_keys(&:to_s)
      @modules = Array(stringified.delete("modules"))
        .map { |raw| Rag::WorkModuleView.from_hash(raw) }
      @sources = Array(stringified.delete("sources"))
        .map { |raw| Rag::SourceCitationView.from_hash(raw) }
      @assumptions = Array(stringified.delete("assumptions"))
        .map { |raw| Rag::AssumptionView.from_hash(raw) }
      super(stringified.slice(
        "total_engineer_days", "duration_weeks", "confidence",
        "reasoning", "insufficient_context_explanation"
      ))
    end

    def insufficient? = confidence == "insufficient"

    # Authoritative sum of all tasks across all modules (used to sanity-check the
    # LLM total and as the basis for the human-verified total).
    def recompute_total = modules.sum(&:subtotal)
  end
end
