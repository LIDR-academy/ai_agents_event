# Mirror of the FastAPI ``EstimationQuery`` Pydantic schema: the structured
# brief distilled from a transcript by the reformulation stage.
module Rag
  class EstimationQueryView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :function, :string
    attribute :sector, :string
    attribute :scale, :string, default: "unknown"
    attribute :country, :string

    attr_reader :technologies, :regulations, :constraints

    def self.from_hash(hash)
      new(hash || {})
    end

    def initialize(attributes = {})
      stringified = (attributes || {}).transform_keys(&:to_s)
      @technologies = Array(stringified.delete("technologies"))
      @regulations  = Array(stringified.delete("regulations"))
      @constraints  = Array(stringified.delete("constraints"))
      super(stringified.slice("function", "sector", "scale", "country"))
    end

    # The hash shape the FastAPI stage endpoints expect back (generate stage).
    def to_payload
      {
        "function" => function,
        "technologies" => technologies,
        "sector" => sector,
        "scale" => scale,
        "country" => country,
        "regulations" => regulations,
        "constraints" => constraints
      }
    end
  end
end
