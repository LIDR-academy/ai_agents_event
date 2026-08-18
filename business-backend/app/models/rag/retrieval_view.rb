# Mirror of the FastAPI ``RetrievalResult`` plus the filters the run used
# (persisted so the retrieval screen is reproducible and re-runnable).
module Rag
  class RetrievalView
    attr_reader :chunks, :low_confidence, :candidates_evaluated, :filters

    def self.from_hash(hash)
      hash = (hash || {}).transform_keys(&:to_s)
      new(
        chunks: Array(hash["chunks"]).map { |raw| Rag::RetrievedChunkView.from_hash(raw) },
        low_confidence: !!hash["low_confidence"],
        candidates_evaluated: hash["candidates_evaluated"].to_i,
        filters: (hash["filters"] || {}).transform_keys(&:to_s)
      )
    end

    def initialize(chunks:, low_confidence:, candidates_evaluated:, filters: {})
      @chunks = chunks
      @low_confidence = low_confidence
      @candidates_evaluated = candidates_evaluated
      @filters = filters
    end

    def soft_fail? = low_confidence || chunks.empty?

    # Chunk hashes ready to feed back into the assemble stage.
    def chunk_payloads = chunks.map(&:to_payload)
  end
end
