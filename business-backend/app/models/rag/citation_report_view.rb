# Mirror of the FastAPI ``CitationReport`` (Session 11 pre-work): the per-line
# referential-integrity audit of a grounded estimate — every cited chunk_id must
# have been retrieved. ``dangling_citations`` are fabricated ids (a citation that
# points nowhere); an empty list means every citation is real.
module Rag
  class CitationReportView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :total_lines, :integer, default: 0
    attribute :grounded_lines, :integer, default: 0
    attribute :dangling_lines, :integer, default: 0
    attribute :insufficient_lines, :integer, default: 0
    attribute :verified_citations, :integer, default: 0

    attr_reader :dangling_citations

    def self.from_hash(hash)
      return nil if hash.blank?

      new(hash)
    end

    def initialize(attributes = {})
      stringified = (attributes || {}).transform_keys(&:to_s)
      @dangling_citations = Array(stringified.delete("dangling_citations")).map(&:to_s)
      super(stringified.slice(
        "total_lines", "grounded_lines", "dangling_lines",
        "insufficient_lines", "verified_citations"
      ))
    end

    def has_dangling? = dangling_citations.any?
  end
end
