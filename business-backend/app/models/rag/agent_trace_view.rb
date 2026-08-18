# Mirror of the FastAPI ``AgentTrace``: the ordered reason→act→observe record of
# the loop, rendered as the STEP N screen in the wizard.
module Rag
  class AgentTraceView
    attr_reader :steps

    def self.from_hash(hash)
      new(hash || {})
    end

    def initialize(payload = {})
      stringified = (payload || {}).transform_keys(&:to_s)
      @steps = Array(stringified["steps"]).map { |raw| Rag::AgentStepView.from_hash(raw) }
    end

    def search_count = steps.count { |s| s.tool == "search_budgets" }

    def total_count = steps.size
  end
end
