# Mirror of the FastAPI ``RetrievedChunk`` schema (S09 retrieval). Distinct from
# Rag::TopChunk (the S07/S08 chunk_id/cosine/text_preview shape) — here ``id``
# is the DB primary key that the generator cites as a source id.
module Rag
  class RetrievedChunkView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :id, :integer
    attribute :content, :string
    attribute :sector, :string
    attribute :project_year, :integer
    attribute :chunk_type, :string
    attribute :distance, :float

    def self.from_hash(hash)
      new((hash || {}).transform_keys(&:to_s).slice(
        "id", "content", "sector", "project_year", "chunk_type", "distance"
      ))
    end

    # Distance as a 0–100 relevance percentage (cosine distance, lower = closer).
    def relevance_pct
      [ [ (1.0 - distance.to_f) * 100, 0 ].max, 100 ].min.round
    end

    def to_payload
      {
        "id" => id, "content" => content, "sector" => sector,
        "project_year" => project_year, "chunk_type" => chunk_type, "distance" => distance
      }
    end
  end
end
