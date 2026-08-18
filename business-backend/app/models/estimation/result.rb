# Mirror of the FastAPI ``EstimationResult`` Pydantic schema (the LLM contract).
class Estimation::Result
  include ActiveModel::Model
  include ActiveModel::Attributes

  OUT_OF_SCOPE_PREFIX = "Out of scope:".freeze

  attribute :summary, :string
  attribute :confidence_pct, :integer
  attribute :total_duration_weeks, :integer
  attribute :total_cost_eur, :integer

  attr_reader :phases

  def initialize(attributes = {})
    stringified = attributes.transform_keys(&:to_s)
    phases_data = stringified.delete("phases") || []
    super(stringified)
    @phases = phases_data.map do |raw|
      raw = raw.transform_keys(&:to_s)
      Estimation::Phase.new(raw.slice("name", "duration_weeks", "cost_eur", "summary"))
    end
  end

  def out_of_scope?
    summary.to_s.start_with?(OUT_OF_SCOPE_PREFIX)
  end
end
