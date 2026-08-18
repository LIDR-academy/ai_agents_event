require "test_helper"

# Session 11 view POROs: the contradictory-sources hour range, the line-level
# citation report and the semantic-gate (hallucination) report.
class RagSession11ViewsTest < ActiveSupport::TestCase
  test "hour range view parses low/high/reason and labels" do
    r = Rag::HourRangeView.from_hash("low" => 40, "high" => 90, "reason" => "sources disagree")
    assert r.present?
    assert_equal "40–90 h", r.label
    assert_equal "sources disagree", r.reason
  end

  test "hour range view is nil when absent" do
    assert_nil Rag::HourRangeView.from_hash(nil)
    assert_nil Rag::HourRangeView.from_hash({})
  end

  test "task item view surfaces a contradiction range" do
    task = Rag::TaskItemView.from_hash(
      "name" => "Notifications", "estimated_hours" => 65, "has_match" => true,
      "hours_range" => { "low" => 40, "high" => 90, "reason" => "scope differs" }
    )
    assert task.contradicted?
    assert_equal "40–90 h", task.hours_range.label
  end

  test "citation report view flags dangling citations" do
    cr = Rag::CitationReportView.from_hash(
      "total_lines" => 3, "grounded_lines" => 2, "dangling_lines" => 1,
      "insufficient_lines" => 0, "verified_citations" => 2,
      "dangling_citations" => ["999"]
    )
    assert cr.has_dangling?
    assert_equal ["999"], cr.dangling_citations
    assert_equal 2, cr.grounded_lines
  end

  test "hallucination report view grades lines and exposes degraded ones" do
    hr = Rag::HallucinationReportView.from_hash(
      "total_lines" => 2, "grounded_lines" => 1, "degraded_lines" => 1, "insufficient_lines" => 0,
      "lines" => [
        { "module" => "Auth", "component" => "OAuth", "status" => "grounded" },
        { "module" => "Auth", "component" => "SCA", "status" => "degraded", "reason" => "claims more than evidence" }
      ]
    )
    assert hr.has_degraded?
    degraded = hr.lines.select(&:degraded?)
    assert_equal 1, degraded.size
    assert_equal "Auth", degraded.first.module_name
    assert_equal :red, degraded.first.band
  end

  test "generation view parses the session 11 reports when present" do
    gv = Rag::GenerationView.from_hash(
      "estimate" => { "confidence" => "high", "reasoning" => "r", "modules" => [] },
      "fabricated_source_ids" => [],
      "coherent" => true,
      "citation_report" => { "total_lines" => 1, "grounded_lines" => 1, "dangling_citations" => [] },
      "hallucination_report" => { "total_lines" => 1, "degraded_lines" => 1, "lines" => [] }
    )
    assert_not_nil gv.citation_report
    assert gv.degraded_grounding?
  end
end
