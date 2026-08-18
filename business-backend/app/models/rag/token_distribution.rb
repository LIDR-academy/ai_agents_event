# Mirror of the FastAPI ``TokenDistribution`` Pydantic schema.
module Rag
  class TokenDistribution
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :min, :integer
    attribute :p50, :float
    attribute :p95, :float
    attribute :max, :integer
  end
end
