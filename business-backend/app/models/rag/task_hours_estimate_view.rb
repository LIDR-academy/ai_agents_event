# Mirror of the FastAPI ``TaskHoursEstimate``: the hours derived for one task by
# vector search over the historical task corpus, with a reliability score and the
# neighbours it was derived from. ``has_match=false`` ⇒ no analog found (red flag).
module Rag
  class TaskHoursEstimateView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :module_name, :string
    attribute :task, :string
    attribute :estimated_hours, :integer
    attribute :reliability, :float
    attribute :has_match, :boolean, default: false
    attribute :dispersion, :float

    attr_reader :neighbors, :hours_range

    def self.from_hash(hash)
      new(hash || {})
    end

    def initialize(attributes = {})
      stringified = (attributes || {}).transform_keys(&:to_s)
      # FastAPI sends the key ``module``; map it to module_name (reserved in Ruby).
      stringified["module_name"] = stringified.delete("module") if stringified.key?("module")
      @neighbors = Array(stringified.delete("neighbors")).map { |raw| Rag::TaskNeighborView.from_hash(raw) }
      # Session 11: the contradictory-sources range (nil when analogs agreed).
      @hours_range = Rag::HourRangeView.from_hash(stringified.delete("hours_range"))
      super(stringified.slice(
        "module_name", "task", "estimated_hours", "reliability", "has_match", "dispersion"
      ))
    end

    def flagged? = has_match == false

    # Session 11: the historical analogs disagreed, so the hours are a range.
    def contradicted? = hours_range&.present?

    def reliability_pct = reliability ? (reliability * 100).round : nil

    def reliability_band
      return :red if flagged?
      return :unknown if reliability.nil?

      reliability >= 0.66 ? :green : :amber
    end
  end
end
