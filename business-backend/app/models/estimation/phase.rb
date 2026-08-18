# Mirror of the FastAPI ``Phase`` Pydantic schema (one line of the estimate).
class Estimation::Phase
  include ActiveModel::Model
  include ActiveModel::Attributes

  attribute :name, :string
  attribute :duration_weeks, :integer
  attribute :cost_eur, :integer
  attribute :summary, :string
end
