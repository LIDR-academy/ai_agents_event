require "test_helper"

# The navbar collapses to Grafo + Agentes on the screens the event demo uses, so nothing
# else in the app is one click away while projecting.
class NavbarTest < ActionDispatch::IntegrationTest
  FULL = [ "Estimación", "Conversación", "RAG Lab", "RAG Wizard", "Grafo", "Agentes", "Ajustes" ].freeze
  HIDDEN = [ "Estimación", "Conversación", "RAG Lab", "RAG Wizard", "Ajustes" ].freeze

  def nav
    response.body[%r{<nav[^>]*>.*?</nav>}m]
  end

  test "the demo screens show only Grafo and Agentes" do
    [ new_rag_graph_estimation_run_path, rag_graph_estimation_runs_path,
      agents_profiles_path, agents_graph_flow_path ].each do |path|
      get path
      assert_response :success
      assert_match "Grafo", nav, path
      assert_match "Agentes", nav, path
      HIDDEN.each { |label| assert_no_match(/#{label}/, nav, "#{label} sigue visible en #{path}") }
    end
  end

  test "every other screen keeps the full navbar" do
    # Sin chat_sessions#new: crea la sesión contra el servicio IA al entrar y necesitaría
    # un stub propio, que no aporta nada a lo que aquí se comprueba.
    [ root_path, new_estimation_path, new_rag_chunking_comparison_path,
      new_rag_estimation_run_path, ai_settings_path ].each do |path|
      get path
      assert_response :success
      FULL.each { |label| assert_match label, nav, "falta #{label} en #{path}" }
    end
  end

  test "a run's show screen keeps the trimmed navbar too" do
    run = Rag::GraphEstimationRun.create!(transcript: "x" * 150, estimation_id: SecureRandom.uuid)
    get rag_graph_estimation_run_path(run)
    assert_response :success
    assert_no_match(/RAG Wizard/, nav)
  end
end
