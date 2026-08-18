# Mirror of the FastAPI ``HourRange`` (Session 11 synthesis). When the historical
# analogs for a task contradict each other (one says 40h, another 90h), the
# servicio IA surfaces the spread as a range plus the reason instead of averaging
# the conflict into one misleading number. ``present?`` is false when the sources
# agreed (no range was emitted).
module Rag
  class HourRangeView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :low, :integer
    attribute :high, :integer
    attribute :reason, :string

    def self.from_hash(hash)
      return nil if hash.blank?

      new((hash || {}).transform_keys(&:to_s).slice("low", "high", "reason"))
    end

    def present? = low.present? && high.present?

    def label = "#{low}–#{high} h"
  end
end
