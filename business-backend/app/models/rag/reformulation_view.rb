# Output of the reformulation stage: the structured brief + the canonical
# search text that gets embedded for retrieval.
module Rag
  class ReformulationView
    attr_reader :query, :search_text

    def self.from_hash(hash)
      hash = (hash || {}).transform_keys(&:to_s)
      new(
        query: Rag::EstimationQueryView.from_hash(hash["query"]),
        search_text: hash["search_text"].to_s
      )
    end

    def initialize(query:, search_text:)
      @query = query
      @search_text = search_text
    end
  end
end
