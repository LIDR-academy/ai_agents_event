# Mirror of the FastAPI ``GenerateResult``: the grounded estimate plus the
# grounding signals the wizard surfaces (fabricated citations / coherence).
module Rag
  class GenerationView
    attr_reader :estimate, :fabricated_source_ids, :coherent, :citation_report, :hallucination_report

    def self.from_hash(hash)
      hash = (hash || {}).transform_keys(&:to_s)
      new(
        estimate: Rag::EstimateView.from_hash(hash["estimate"]),
        fabricated_source_ids: Array(hash["fabricated_source_ids"]).map(&:to_i),
        coherent: hash.fetch("coherent", true),
        # Session 11: the per-line citation audit and the semantic gate (present
        # only on the grounded generate path; nil for the structure-only stage).
        citation_report: Rag::CitationReportView.from_hash(hash["citation_report"]),
        hallucination_report: Rag::HallucinationReportView.from_hash(hash["hallucination_report"])
      )
    end

    def initialize(estimate:, fabricated_source_ids:, coherent:, citation_report: nil, hallucination_report: nil)
      @estimate = estimate
      @fabricated_source_ids = fabricated_source_ids
      @coherent = coherent
      @citation_report = citation_report
      @hallucination_report = hallucination_report
    end

    def fabricated_citations? = fabricated_source_ids.any?

    def grounding_clean? = !fabricated_citations? && coherent

    # Session 11: the semantic gate flagged at least one grounded line as
    # unsupported by its evidence (a real citation, an invented number).
    def degraded_grounding? = hallucination_report&.has_degraded?
  end
end
