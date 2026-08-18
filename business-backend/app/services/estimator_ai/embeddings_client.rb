# Context client for the RAG embeddings pipeline (Session 7): chunking
# strategy comparison over the historical-budget corpus.
#
# Paid strategies (propositional, contextual_retrieval) take minutes — callers
# should pass a generous timeout: ``EmbeddingsClient.new(timeout: 600)``.
module EstimatorAi
  class EmbeddingsClient < BaseClient
    # POST /embeddings/compare. ``budgets`` is an array of Budget hashes (the
    # corpus travels verbatim; FastAPI validates it with Pydantic). Empty
    # ``queries`` skips retrieval (stats only); empty ``strategies`` compares
    # all eight.
    def compare_chunking(budgets:, queries: [], strategies: [], top_k: 3)
      raise ArgumentError, "budgets must be a non-empty array" if Array(budgets).empty?

      response = json_conn.post("/embeddings/compare", {
        budgets: budgets, queries: queries, strategies: strategies, top_k: top_k
      })
      handle_response(response)
    end
  end
end
