require "test_helper"

class EstimationRequestTest < ActiveSupport::TestCase
  def valid_attrs
    {
      description: "App movil con login, chat y notificaciones push para iOS y Android",
      project_type: "mobile_app",
      detail_level: "medium",
      output_format: "phases_table"
    }
  end

  test "valid with all enums correct" do
    assert Estimation::Request.new(valid_attrs).valid?
  end

  test "default detail_level is medium" do
    assert_equal "medium", Estimation::Request.new.detail_level
  end

  test "default output_format is phases_table" do
    assert_equal "phases_table", Estimation::Request.new.output_format
  end

  test "invalid without description" do
    req = Estimation::Request.new(valid_attrs.merge(description: nil))
    assert_not req.valid?
    assert_includes req.errors[:description], "can't be blank"
  end

  test "invalid with description shorter than 20 chars" do
    req = Estimation::Request.new(valid_attrs.merge(description: "too short"))
    assert_not req.valid?
    assert req.errors[:description].any? { |m| m.include?("too short") || m.include?("is too short") }
  end

  test "invalid with description longer than 80000 chars" do
    req = Estimation::Request.new(valid_attrs.merge(description: "x" * 80001))
    assert_not req.valid?
  end

  test "invalid with project_type out of enum" do
    req = Estimation::Request.new(valid_attrs.merge(project_type: "spaceship"))
    assert_not req.valid?
    assert_includes req.errors[:project_type], "is not included in the list"
  end

  test "invalid with detail_level out of enum" do
    req = Estimation::Request.new(valid_attrs.merge(detail_level: "exhaustive"))
    assert_not req.valid?
  end

  test "invalid with output_format out of enum" do
    req = Estimation::Request.new(valid_attrs.merge(output_format: "yaml"))
    assert_not req.valid?
  end

  test "to_payload returns symbol-keyed hash matching FastAPI schema" do
    payload = Estimation::Request.new(valid_attrs).to_payload
    assert_equal valid_attrs.keys.sort, payload.keys.sort
    assert payload.keys.all? { |k| k.is_a?(Symbol) }
  end
end
