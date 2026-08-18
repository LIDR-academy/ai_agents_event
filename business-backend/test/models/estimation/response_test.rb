require "test_helper"

class EstimationResponseTest < ActiveSupport::TestCase
  def sample_hash
    {
      "result" => {
        "summary" => "Standard B2B SaaS for managing equipment loans.",
        "confidence_pct" => 70,
        "phases" => [
          { "name" => "Discovery", "duration_weeks" => 1, "cost_eur" => 5000,
            "summary" => "Workshops and scoping." },
          { "name" => "Build", "duration_weeks" => 6, "cost_eur" => 20000,
            "summary" => "Core implementation." }
        ],
        "total_duration_weeks" => 7,
        "total_cost_eur" => 25000
      },
      "prompt_version" => "v1",
      "cached" => false
    }
  end

  test "from_hash builds an Estimation::Response with a nested Estimation::Result" do
    response = Estimation::Response.from_hash(sample_hash)
    assert_equal "v1", response.prompt_version
    assert_equal false, response.cached
    assert_kind_of Estimation::Result, response.result
    assert_equal 25_000, response.result.total_cost_eur
    assert_equal 70, response.result.confidence_pct
    assert_equal 2, response.result.phases.size
    assert_kind_of Estimation::Phase, response.result.phases.first
    assert_equal "Discovery", response.result.phases.first.name
    assert_equal 5_000, response.result.phases.first.cost_eur
  end

  test "cached flag is parsed correctly" do
    payload = sample_hash.merge("cached" => true)
    response = Estimation::Response.from_hash(payload)
    assert_equal true, response.cached
  end

  test "result.out_of_scope? detects the Out of scope prefix" do
    payload = sample_hash.deep_dup
    payload["result"]["summary"] = "Out of scope: the description is too vague."
    payload["result"]["confidence_pct"] = 15
    response = Estimation::Response.from_hash(payload)
    assert response.result.out_of_scope?
  end

  test "result.out_of_scope? is false for normal summaries" do
    response = Estimation::Response.from_hash(sample_hash)
    assert_not response.result.out_of_scope?
  end
end
