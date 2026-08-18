# Mirror of the FastAPI ``TaskHoursResult``: the per-task hours estimates for a
# whole run, plus convenience counts for the hours-review screen.
module Rag
  class TaskHoursView
    attr_reader :tasks

    def self.from_hash(hash)
      new(hash || {})
    end

    def initialize(payload = {})
      stringified = (payload || {}).transform_keys(&:to_s)
      @tasks = Array(stringified["tasks"]).map { |raw| Rag::TaskHoursEstimateView.from_hash(raw) }
    end

    def matched_count = tasks.count(&:has_match)

    def flagged_count = tasks.count(&:flagged?)

    def contradicted_count = tasks.count(&:contradicted?)

    def total_count = tasks.size
  end
end
