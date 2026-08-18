# ActiveRecord root of the RAG context (Session 7). Persists each comparison
# run — request params + full FastAPI response as JSONB — so expensive runs
# (contextual_retrieval ≈ $0.14 / 3 min) can be revisited without re-paying.
module Rag
  class ChunkingComparison < ApplicationRecord
    self.table_name = "chunking_comparisons"

    validates :strategies, presence: true
    validates :top_k, numericality: { in: 1..10 }

    def to_response
      Rag::ComparisonResponse.from_hash(response_payload)
    end

    def strategy_labels
      strategies.map { |name| Rag::Strategy.label_for(name) }
    end

    def cost_total_usd
      to_response.total_ingestion_cost_usd
    end

    def duration_seconds
      duration_ms ? (duration_ms / 1000.0).round(1) : nil
    end
  end
end
