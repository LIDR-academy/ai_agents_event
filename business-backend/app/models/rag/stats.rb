# Mirror of the FastAPI ``ChunkingStats`` Pydantic schema: per-strategy corpus
# statistics (chunk counts, token percentiles, degeneracy flags, ingest cost).
module Rag
  class Stats
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :strategy, :string
    attribute :n_chunks, :integer
    attribute :n_orphan_chunks, :integer   # chunks < 20 tokens
    attribute :n_obese_chunks, :integer    # chunks > 800 tokens
    attribute :ingestion_cost_usd, :float
    attribute :ingestion_seconds, :float

    attr_reader :token_distribution

    def initialize(attributes = {})
      stringified = attributes.transform_keys(&:to_s)
      distribution = stringified.delete("token_distribution") || {}
      super(stringified)
      @token_distribution = Rag::TokenDistribution.new(distribution)
    end

    def orphans? = n_orphan_chunks.to_i.positive?
    def obese? = n_obese_chunks.to_i.positive?
    def free? = ingestion_cost_usd.to_f.zero?
  end
end
