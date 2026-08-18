# Mirror of the FastAPI ``HallucinationReport`` (Session 11 semantic gate). Where
# the CitationReport proves a citation is REAL, this grades whether the number is
# ENTAILED by it: each grounded line is graded grounded / degraded / insufficient
# by a deterministic numeric anchor plus a strict judge. ``degraded`` lines cite a
# real source but claim more than it supports — a hallucination wearing a citation.
module Rag
  class HallucinationReportView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :total_lines, :integer, default: 0
    attribute :grounded_lines, :integer, default: 0
    attribute :degraded_lines, :integer, default: 0
    attribute :insufficient_lines, :integer, default: 0

    attr_reader :lines

    def self.from_hash(hash)
      return nil if hash.blank?

      new(hash)
    end

    def initialize(attributes = {})
      stringified = (attributes || {}).transform_keys(&:to_s)
      @lines = Array(stringified.delete("lines")).map { |raw| LineGateView.from_hash(raw) }
      super(stringified.slice(
        "total_lines", "grounded_lines", "degraded_lines", "insufficient_lines"
      ))
    end

    def has_degraded? = degraded_lines.positive?

    # One graded estimate line.
    class LineGateView
      include ActiveModel::Model
      include ActiveModel::Attributes

      attribute :module_name, :string
      attribute :component, :string
      attribute :status, :string # grounded | degraded | insufficient
      attribute :numeric_deviation, :float
      attribute :reason, :string

      def self.from_hash(hash)
        stringified = (hash || {}).transform_keys(&:to_s)
        stringified["module_name"] = stringified.delete("module") if stringified.key?("module")
        new(stringified.slice("module_name", "component", "status", "numeric_deviation", "reason"))
      end

      def degraded? = status == "degraded"

      # Traffic-light band, reusing the wizard's convention (green/amber/red).
      def band
        case status
        when "grounded" then :green
        when "degraded" then :red
        else :amber
        end
      end
    end
  end
end
