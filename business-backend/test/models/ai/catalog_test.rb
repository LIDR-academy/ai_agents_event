require "test_helper"

class AiCatalogTest < ActiveSupport::TestCase
  def sample_hash
    {
      "models" => {
        "PRIMARY_MODEL" => { "effective" => "gpt-4o", "default" => "gpt-4o-mini", "overridden" => true },
        "CRITIC_MODEL" => { "effective" => "gpt-4o-mini", "default" => "gpt-4o-mini", "overridden" => false }
      },
      "available_models" => [ "gpt-4o-mini", "gpt-4o" ],
      "embedding_model" => "text-embedding-3-small",
      "embedding_model_note" => "Read-only."
    }
  end

  test "from_hash builds typed model configs in catalog order" do
    catalog = Ai::Catalog.from_hash(sample_hash)

    assert_equal %w[PRIMARY_MODEL CRITIC_MODEL], catalog.model_configs.map(&:key)
    primary = catalog.model_configs.first
    assert_kind_of Ai::ModelConfig, primary
    assert_equal "gpt-4o", primary.effective
    assert primary.overridden
    assert_equal "Modelo primario", primary.label
  end

  test "primary_model and any_overridden? convenience readers" do
    catalog = Ai::Catalog.from_hash(sample_hash)
    assert_equal "gpt-4o", catalog.primary_model
    assert catalog.any_overridden?
  end

  test "model config catalog covers the seven runtime knobs" do
    assert_equal 7, Ai::ModelConfig::CATALOG.size
    assert_includes Ai::ModelConfig::KEYS, "CONTEXTUAL_CHUNKER_MODEL"
  end

  test "unknown key still renders with the raw key as label" do
    config = Ai::ModelConfig.from_hash("FUTURE_KEY", { "effective" => "x", "default" => "x" })
    assert_equal "FUTURE_KEY", config.label
    assert_nil config.description
  end
end
