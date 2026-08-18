require "test_helper"

class EstimationTest < ActiveSupport::TestCase
  def payload
    {
      "result" => {
        "summary" => "Mobile app build, 12 weeks.",
        "confidence_pct" => 75,
        "phases" => [
          { "name" => "Discovery", "duration_weeks" => 1, "cost_eur" => 5_000, "summary" => "Scoping." },
          { "name" => "Build",     "duration_weeks" => 6, "cost_eur" => 20_000, "summary" => "Core." }
        ],
        "total_duration_weeks" => 7,
        "total_cost_eur" => 25_000
      },
      "prompt_version" => "v1",
      "cached" => false
    }
  end

  def valid_attrs(overrides = {})
    {
      description:      "A mobile app with login, chat and notifications.",
      project_type:     "mobile_app",
      detail_level:     "medium",
      output_format:    "phases_table",
      response_payload: payload,
      prompt_version:   "v1",
      cached:           false
    }.merge(overrides)
  end

  test "valid with required attributes" do
    assert Estimation.new(valid_attrs).valid?
  end

  test "to_response rebuilds an Estimation::Response from the stored jsonb" do
    estimation = Estimation.create!(valid_attrs)
    response = estimation.to_response
    assert_kind_of Estimation::Response, response
    assert_equal "v1", response.prompt_version
    assert_equal 25_000, response.result.total_cost_eur
    assert_equal "Discovery", response.result.phases.first.name
  end

  test "description_preview truncates long descriptions" do
    long = "x" * 200
    estimation = Estimation.new(valid_attrs(description: long))
    assert_equal 80, estimation.description_preview.length
  end
end
