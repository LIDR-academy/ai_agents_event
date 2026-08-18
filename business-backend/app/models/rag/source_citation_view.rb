# Mirror of the FastAPI ``SourceCitation``: a reference from the estimate back
# to a retrieved chunk (by DB id).
module Rag
  class SourceCitationView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :source_id, :integer
    attribute :relevance, :string   # primary | supporting | tangential
    attribute :used_for, :string

    def self.from_hash(hash)
      new((hash || {}).transform_keys(&:to_s).slice("source_id", "relevance", "used_for"))
    end
  end
end
