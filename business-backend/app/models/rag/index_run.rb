# ActiveRecord root of the Session 11 corpus-expansion flow. Persists one async
# "add new information to the vector DB" run: the FastAPI job_id, how many
# documents were submitted, the latest job status/progress, and the before/after
# corpus stats (per collection) so the UI can show the corpus growing. Mirrors
# Rag::ChunkingComparison (AR root + JSONB payload).
module Rag
  class IndexRun < ApplicationRecord
    self.table_name = "index_runs"

    validates :job_id, presence: true

    TERMINAL_STATUSES = %w[completed failed].freeze

    def finished? = TERMINAL_STATUSES.include?(status)

    def before_collections
      Array(before_stats["collections"]).map { |c| CollectionStat.new(c) }
    end

    def after_collections
      Array(after_stats["collections"]).map { |c| CollectionStat.new(c) }
    end

    # Chunks added across the whole corpus (after − before), once known.
    def chunks_added
      after = after_stats["total_chunks"]
      before = before_stats["total_chunks"]
      return nil if after.nil? || before.nil?

      after.to_i - before.to_i
    end

    # A tiny value object so the views render typed fields, not raw hashes.
    CollectionStat = Struct.new(:raw) do
      def collection = raw["collection"]
      def documents = raw["documents"].to_i
      def chunks = raw["chunks"].to_i
      def hnsw_indexed? = raw["hnsw_indexed"] == true
    end
  end
end
