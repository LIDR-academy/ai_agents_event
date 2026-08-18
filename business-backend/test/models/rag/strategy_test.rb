require "test_helper"

class RagStrategyTest < ActiveSupport::TestCase
  # The eight names served by the estimator's /embeddings/compare, in
  # canonical order (mirror of ALL_STRATEGIES in app/dependencies.py).
  EXPECTED = %w[
    structural fixed_size recursive sentence_window hierarchical
    semantic propositional contextual_retrieval
  ].freeze

  test "catalog covers the eight strategies" do
    assert_equal EXPECTED.sort, Rag::Strategy::ALL_NAMES.sort
    assert_equal 8, Rag::Strategy::CATALOG.size
  end

  test "defaults are the free fast trio" do
    assert_equal %w[structural fixed_size recursive], Rag::Strategy.defaults
  end

  test "expensive_selected? flags the paid LLM strategies" do
    assert Rag::Strategy.expensive_selected?(%w[structural contextual_retrieval])
    assert Rag::Strategy.expensive_selected?(%w[propositional])
    assert_not Rag::Strategy.expensive_selected?(%w[structural semantic hierarchical])
  end

  test "label_for falls back to the raw name" do
    assert_equal "Contextual retrieval", Rag::Strategy.label_for("contextual_retrieval")
    assert_equal "unknown", Rag::Strategy.label_for("unknown")
  end
end
