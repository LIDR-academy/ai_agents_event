# Mirror of the FastAPI ``AssembleResult``: the assembled <source> XML block
# plus the chunks that survived the token budget.
module Rag
  class AugmentationView
    attr_reader :context_block, :kept_chunks, :dropped_count, :token_count

    def self.from_hash(hash)
      hash = (hash || {}).transform_keys(&:to_s)
      new(
        context_block: hash["context_block"].to_s,
        kept_chunks: Array(hash["kept_chunks"]).map { |raw| Rag::RetrievedChunkView.from_hash(raw) },
        dropped_count: hash["dropped_count"].to_i,
        token_count: hash["token_count"].to_i
      )
    end

    def initialize(context_block:, kept_chunks:, dropped_count:, token_count:)
      @context_block = context_block
      @kept_chunks = kept_chunks
      @dropped_count = dropped_count
      @token_count = token_count
    end

    def kept_chunk_payloads = kept_chunks.map(&:to_payload)
  end
end
