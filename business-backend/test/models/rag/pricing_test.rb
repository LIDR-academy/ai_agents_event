require "test_helper"

class RagPricingTest < ActiveSupport::TestCase
  def pricing(hours:, rate: 75, pct: 15)
    Rag::Pricing.new(hours: hours, rate_eur_per_hour: rate, contingency_pct: pct)
  end

  test "base, contingency and total" do
    p = pricing(hours: 646.0)

    assert_in_delta 48_450.0, p.base, 0.001
    assert_in_delta 7_267.5,  p.contingency, 0.001
    assert_in_delta 55_717.5, p.total, 0.001
  end

  test "half hours survive the calculation" do
    # The whole reason estimated_hours is a float: 646.5 h is a real value the graph emits.
    p = pricing(hours: 646.5, pct: 0)

    assert_in_delta 48_487.5, p.base, 0.001
    assert_in_delta 48_487.5, p.total, 0.001
  end

  test "no contingency leaves the base untouched" do
    p = pricing(hours: 100.0, pct: 0)

    assert_equal 0.0, p.contingency
    assert_equal p.base, p.total
    assert_not p.contingency?
  end

  test "a zero rate means the run is simply not priced" do
    p = pricing(hours: 100.0, rate: 0)

    assert_not p.priced?
    assert_equal 0.0, p.total
  end

  test "the payload sent to the AI service is rounded whole euros" do
    payload = pricing(hours: 646.5).to_payload

    assert_equal "EUR", payload["currency"]
    assert_equal 75, payload["rate_eur_per_hour"]
    assert_equal 15, payload["contingency_pct"]
    assert_equal 48_488, payload["base_eur"]
    assert_equal 7_273,  payload["contingency_eur"]
    assert_equal 55_761, payload["total_eur"]
    # Pre-formatted so the prose and the tables print the same string.
    assert_equal "55.761 €", payload["total_display"]
    assert_equal "48.488 €", payload["base_display"]
    assert_equal "75 €/h", payload["rate_display"]
  end

  # The property that makes the result screen trustworthy: the per-module column has to add
  # up to the headline base. It only does because nothing is rounded until display.
  test "the per-module breakdown sums to the base" do
    run = Rag::GraphEstimationRun.new(
      transcript: "x" * 150, estimation_id: SecureRandom.uuid,
      rate_eur_per_hour: 75, contingency_pct: 15,
      estimate: {
        "total_engineer_hours" => 40.5,
        "modules" => [
          { "name" => "A", "tasks" => [ { "name" => "a1", "estimated_hours" => 7.5 },
                                        { "name" => "a2", "estimated_hours" => 13 } ] },
          { "name" => "B", "tasks" => [ { "name" => "b1", "estimated_hours" => 20 } ] }
        ]
      }
    )

    modules_total = run.estimate_modules.sum(&:subtotal_cost)

    assert_in_delta run.pricing.base, modules_total, 0.01
    assert_in_delta 40.5, run.estimate_modules.sum(&:subtotal_hours), 0.001,
                    "las medias horas no pueden perderse por el camino"
  end

  test "hours format drops the noise decimal but keeps a real one" do
    assert_equal "152", Rag::Hours.format(152.0)
    assert_equal "7.5", Rag::Hours.format(7.5)
    assert_nil Rag::Hours.format(nil)
  end
end
