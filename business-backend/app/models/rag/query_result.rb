# Mirror of the FastAPI ``QueryResult`` Pydantic schema: the top-k chunks one
# strategy retrieves for one query.
module Rag
  class QueryResult
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :strategy, :string
    attribute :query, :string

    attr_reader :top_k

    def initialize(attributes = {})
      stringified = attributes.transform_keys(&:to_s)
      chunks = stringified.delete("top_k") || []
      super(stringified)
      @top_k = chunks.map { |raw| Rag::TopChunk.new(raw.transform_keys(&:to_s)) }
    end
  end
end
