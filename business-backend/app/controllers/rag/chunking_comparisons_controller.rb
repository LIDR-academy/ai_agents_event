# Chunking Lab (Session 7): compares chunking strategies over the bundled
# historical-budget corpus via POST /embeddings/compare and persists each run
# so expensive ones can be revisited without re-paying.
#
# Synchronous by design (no job infra in this client): the default selection
# (free strategies) answers in seconds; the paid ones are opt-in, flagged in
# the form, and covered by a longer per-call timeout.
module Rag
  class ChunkingComparisonsController < ApplicationController
    # Full runs with propositional/contextual_retrieval take minutes; the
    # global 180s default stays untouched for the estimate flows.
    COMPARE_TIMEOUT_SECONDS = 600

    def index
      @comparisons = Rag::ChunkingComparison.order(created_at: :desc).limit(20)
    end

    def new
      @selected_strategies = Rag::Strategy.defaults
      @queries = []
      @top_k = 3
    end

    def create
      @selected_strategies = Array(params[:strategies]).select do |name|
        Rag::Strategy::ALL_NAMES.include?(name)
      end
      @queries = Array(params[:queries]).map(&:strip).compact_blank.uniq
      @top_k = params[:top_k].to_i.clamp(1, 10)

      if @selected_strategies.empty?
        flash.now[:alert] = "Selecciona al menos una estrategia."
        return render :new, status: :unprocessable_entity
      end

      budgets = EstimatorAi::BudgetCorpus.budgets
      started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      payload = EstimatorAi::EmbeddingsClient.new(timeout: COMPARE_TIMEOUT_SECONDS).compare_chunking(
        budgets: budgets,
        queries: @queries,
        strategies: @selected_strategies,
        top_k: @top_k
      )
      duration_ms = ((Process.clock_gettime(Process::CLOCK_MONOTONIC) - started) * 1000).round

      @comparison = Rag::ChunkingComparison.create!(
        strategies: @selected_strategies,
        queries: @queries,
        top_k: @top_k,
        corpus_label: EstimatorAi::BudgetCorpus::LABEL,
        corpus_count: budgets.size,
        response_payload: payload,
        duration_ms: duration_ms
      )

      redirect_to rag_chunking_comparison_path(@comparison)
    rescue EstimatorAi::InvalidRequest => e
      flash.now[:alert] = "Petición inválida: #{e.message}"
      render :new, status: :unprocessable_entity
    rescue EstimatorAi::ServerError => e
      flash.now[:alert] = server_error_message(e)
      render :new, status: :service_unavailable
    rescue Faraday::TimeoutError, Faraday::ConnectionFailed => e
      flash.now[:alert] = "El servicio IA no respondió a tiempo (#{COMPARE_TIMEOUT_SECONDS}s). " \
                          "Las estrategias de pago (propositional, contextual_retrieval) pueden " \
                          "tardar varios minutos — prueba con menos estrategias. (#{e.class})"
      render :new, status: :service_unavailable
    end

    def show
      @comparison = Rag::ChunkingComparison.find(params[:id])
      @response = @comparison.to_response
    end

    private

    def server_error_message(error)
      message = error.message.to_s
      if message.include?("OPENAI_API_KEY") || message.include?("ANTHROPIC_API_KEY")
        "El run necesita una API key que no está configurada en el servicio IA: #{message}"
      elsif message.include?("not available")
        "El servicio de embeddings no está disponible en el estimator (¿falta la API key?)."
      else
        "Error del servicio IA: #{message}"
      end
    end
  end
end
