# Mirror of the FastAPI ``AgentStep``: one reason→act→observe step of the loop.
# ``tool_args`` is free-form (whatever the model passed), so it is kept raw and
# rendered as pretty JSON rather than cast.
module Rag
  class AgentStepView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :step, :integer
    attribute :reasoning_summary, :string
    attribute :tool, :string
    attribute :observation, :string

    attr_reader :tool_args

    def self.from_hash(hash)
      new(hash || {})
    end

    def initialize(attributes = {})
      stringified = (attributes || {}).transform_keys(&:to_s)
      @tool_args = stringified.delete("tool_args") || {}
      super(stringified.slice("step", "reasoning_summary", "tool", "observation"))
    end

    # The ``tool(args)`` action line, echoing the console STEP N format.
    def action = "#{tool}(#{tool_args.to_json})"

    def reasoning = reasoning_summary.presence || "(no reasoning summary emitted)"
  end
end
