require "test_helper"

# Session 12 view POROs: the hand-written agent's reason→act→observe trace, now
# carried inside the phase JSONB it drove (generation / task_hours) and rendered
# as the STEP N screen under those wizard steps.
class RagAgentTraceViewTest < ActiveSupport::TestCase
  def trace_payload
    {
      "steps" => [
        {
          "step" => 1,
          "reasoning_summary" => "Search analogs for the flagged task.",
          "tool" => "search_budgets",
          "tool_args" => { "query" => "logistics tracking", "filters" => { "sectors" => ["logistics"] } },
          "observation" => "2 historical items; hours=[940, 1150]"
        },
        {
          "step" => 2,
          "reasoning_summary" => nil,
          "tool" => "derive_task_hours",
          "tool_args" => { "module" => "Core", "task" => "Tracking", "neighbors" => [] },
          "observation" => "Tracking: 1050h (reliability 0.7) from 2 analogs"
        }
      ]
    }
  end

  test "parses the trace steps with counts" do
    trace = Rag::AgentTraceView.from_hash(trace_payload)
    assert_equal 2, trace.total_count
    assert_equal 1, trace.search_count

    first = trace.steps.first
    assert_equal 1, first.step
    assert_equal "search_budgets", first.tool
    assert_includes first.action, "search_budgets("
    assert_includes first.action, "logistics"

    # A step with no reasoning summary falls back to a placeholder.
    assert_equal "(no reasoning summary emitted)", trace.steps.second.reasoning
  end

  test "estimation run exposes the phase trace readers from the JSONB" do
    run = Rag::EstimationRun.new(
      transcript: "x" * 200,
      generation: { "estimate" => { "modules" => [] }, "agent_trace" => { "steps" => [
        { "step" => 1, "tool" => "propose_structure", "tool_args" => { "modules" => 3 },
          "observation" => "3 modules / 9 tasks" }
      ] } },
      task_hours: { "tasks" => [], "agent_trace" => trace_payload }
    )

    gen_trace = run.generation_agent_trace
    assert_equal 1, gen_trace.total_count
    assert_equal "propose_structure", gen_trace.steps.first.tool

    hours_trace = run.hours_agent_trace
    assert_equal 2, hours_trace.total_count
    assert_equal 1, hours_trace.search_count
  end

  test "phase trace readers are nil on the deterministic path (no agent_trace key)" do
    run = Rag::EstimationRun.new(
      transcript: "x" * 200,
      generation: { "estimate" => { "modules" => [] } },
      task_hours: { "tasks" => [] }
    )
    assert_nil run.generation_agent_trace
    assert_nil run.hours_agent_trace
  end
end
