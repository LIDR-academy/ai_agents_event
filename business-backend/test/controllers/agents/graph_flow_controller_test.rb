require "test_helper"

# Session 13 — the read-only visual resource of the multi-agent graph flow. It
# renders the static Agents::GraphFlow catalog with no call to the AI service, so
# no HTTP stub is needed and the screen must render even when the service is down.
class AgentsGraphFlowControllerTest < ActionDispatch::IntegrationTest
  test "show renders the flow with every node, its role and the notation legend" do
    get agents_graph_flow_path
    assert_response :success

    # Every node label from the catalog appears (agents + gates + fan-out + join).
    # Escape first: labels/roles may carry HTML-significant characters (e.g. "&").
    Agents::GraphFlow::NODES.each do |node|
      assert_includes response.body, ERB::Util.html_escape(node.label), "missing node label: #{node.label}"
      assert_includes response.body, ERB::Util.html_escape(node.role), "missing node role: #{node.role}"
    end

    # The LangGraph notation is taught explicitly.
    assert_match "Command(goto", response.body
    assert_match "interrupt()", response.body
    assert_match "Send", response.body
    # The two boundaries of the flow.
    assert_match "START", response.body
    assert_match "END", response.body
  end
end
