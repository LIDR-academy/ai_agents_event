# Mirror of the FastAPI ``Assumption``: an estimate component NOT backed by any
# retrieved source.
module Rag
  class AssumptionView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :description, :string
    attribute :impact, :string   # high | medium | low
    attribute :rationale, :string

    def self.from_hash(hash)
      new((hash || {}).transform_keys(&:to_s).slice("description", "impact", "rationale"))
    end
  end
end
