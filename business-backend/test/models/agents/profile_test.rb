require "test_helper"

# Session 12 — agent profile presets: validations, the config→override-body
# mapping, and the single-default invariant.
class AgentsProfileTest < ActiveSupport::TestCase
  test "requires a name and a valid agent_type" do
    p = Agents::Profile.new(agent_type: "handwritten")
    assert_not p.valid?
    assert_includes p.errors.attribute_names, :name

    p = Agents::Profile.new(name: "X", agent_type: "bogus")
    assert_not p.valid?
    assert_includes p.errors.attribute_names, :agent_type
  end

  test "name is unique per agent_type (case-insensitive)" do
    Agents::Profile.create!(name: "Estándar")
    dup = Agents::Profile.new(name: "estándar")
    assert_not dup.valid?
  end

  test "validates knob ranges and effort inclusion via the config bag" do
    p = Agents::Profile.new(name: "Bad")
    p.config = { "reasoning_effort" => "turbo", "max_iterations" => 99, "search_top_k" => 0,
                 "search_distance_threshold" => 5 }
    assert_not p.valid?
    %i[reasoning_effort max_iterations search_top_k search_distance_threshold].each do |attr|
      assert_includes p.errors.attribute_names, attr
    end
  end

  test "config_payload compacts blanks and coerces types" do
    p = Agents::Profile.new(name: "Veloz")
    p.assign_config("model" => "gpt-5-mini", "reasoning_effort" => "low",
                    "max_iterations" => "6", "search_top_k" => "", "search_distance_threshold" => "0.4")
    assert p.valid?, p.errors.full_messages.to_sentence
    payload = p.config_payload
    assert_equal "gpt-5-mini", payload["model"]
    assert_equal 6, payload["max_iterations"]           # integer
    assert_equal 0.4, payload["search_distance_threshold"] # float
    assert_not payload.key?("search_top_k")              # blank dropped → service default
  end

  test "saving a default demotes the previous default of the same agent_type" do
    a = Agents::Profile.create!(name: "A", is_default: true)
    b = Agents::Profile.create!(name: "B", is_default: true)
    assert b.reload.is_default
    assert_not a.reload.is_default
  end

  test "assign_config keeps only known knob keys" do
    p = Agents::Profile.new(name: "Filtered")
    p.assign_config("model" => "gpt-5", "evil" => "rm -rf", "reasoning_effort" => "high")
    assert_equal({ "model" => "gpt-5", "reasoning_effort" => "high" }, p.config)
  end

  test "accepts an image avatar and rejects a non-image" do
    png = Rack::Test::UploadedFile.new("test/fixtures/files/avatar.png", "image/png")
    p = Agents::Profile.new(name: "Con foto")
    p.avatar.attach(png)
    assert p.valid?, p.errors.full_messages.to_sentence

    bad = Agents::Profile.new(name: "Con texto")
    bad.avatar.attach(io: StringIO.new("not an image"), filename: "x.txt", content_type: "text/plain")
    assert_not bad.valid?
    assert_includes bad.errors.attribute_names, :avatar
  end
end
