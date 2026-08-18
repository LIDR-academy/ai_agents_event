# Mirror of the FastAPI ``TaskNeighbor``: one historical task that backed a
# per-task hours estimate (shown for transparency in the hours step).
module Rag
  class TaskNeighborView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :source_id, :integer
    attribute :budget_id, :string
    attribute :estimated_hours, :integer
    attribute :distance, :float

    def self.from_hash(hash)
      new((hash || {}).transform_keys(&:to_s).slice(
        "source_id", "budget_id", "estimated_hours", "distance"
      ))
    end

    # Cosine distance → 0..100 closeness, for a compact label.
    def closeness_pct = [ (100 - distance.to_f * 100).round, 0 ].max
  end
end
