# Corpus / Índice (Session 11): add NEW information to the vector DB and watch it
# get indexed. Unlike the Chunking Lab (synchronous), expansion is an async job
# in the IA service: create → POST 202 + job_id, then the show page POLLS the
# `status` member action until the job finishes and the corpus stats grow.
module Rag
  class IndexRunsController < ApplicationController
    EXPAND_TIMEOUT_SECONDS = 120

    def index
      @runs = Rag::IndexRun.order(created_at: :desc).limit(20)
      @stats = fetch_stats
    rescue EstimatorAi::Error, Faraday::Error => e
      @runs ||= Rag::IndexRun.order(created_at: :desc).limit(20)
      @stats = nil
      flash.now[:alert] = "No se pudieron leer las estadísticas del corpus: #{e.class}"
    end

    def new
      @chunk_type = "budget_component"
      @documents_json = EXAMPLE_JSON
    end

    def create
      @chunk_type = params[:chunk_type].presence || "budget_component"
      @documents_json = params[:documents_json].to_s
      documents = parse_documents(@documents_json)

      if documents.blank?
        flash.now[:alert] = "Pega al menos un documento (objeto JSON o array de objetos)."
        return render :new, status: :unprocessable_entity
      end

      before = fetch_stats
      response = client.start_expansion(documents: documents, chunk_type: @chunk_type)
      run = Rag::IndexRun.create!(
        job_id: response["job_id"],
        chunk_type: @chunk_type,
        submitted_count: response["documents_total"].to_i,
        status: response["status"].presence || "pending",
        before_stats: before || {}
      )
      redirect_to rag_index_run_path(run)
    rescue JSON::ParserError => e
      flash.now[:alert] = "JSON inválido: #{e.message}"
      render :new, status: :unprocessable_entity
    rescue EstimatorAi::IndexClient::InvalidBatch => e
      flash.now[:alert] = "El servicio IA rechazó el lote (¿faltan campos del presupuesto?): #{e.message}"
      render :new, status: :unprocessable_entity
    rescue EstimatorAi::Error => e
      flash.now[:alert] = "Error del servicio IA: #{e.message}"
      render :new, status: :service_unavailable
    rescue Faraday::TimeoutError, Faraday::ConnectionFailed => e
      flash.now[:alert] = "El servicio IA no respondió a tiempo. (#{e.class})"
      render :new, status: :service_unavailable
    end

    def show
      @run = Rag::IndexRun.find(params[:id])
    end

    # Polled by the show page (JSON). Refreshes the job snapshot and, once the job
    # finishes, captures the grown corpus stats so the UI shows the delta.
    def status
      @run = Rag::IndexRun.find(params[:id])
      unless @run.finished?
        snapshot = client.job_status(@run.job_id)
        attrs = { status: snapshot["status"], documents_processed: snapshot["documents_processed"].to_i }
        attrs[:after_stats] = fetch_stats || {} if Rag::IndexRun::TERMINAL_STATUSES.include?(snapshot["status"])
        @run.update!(attrs)
      end
      render json: {
        status: @run.status,
        finished: @run.finished?,
        documents_processed: @run.documents_processed,
        submitted_count: @run.submitted_count,
        chunks_added: @run.chunks_added
      }
    rescue EstimatorAi::Error, Faraday::Error => e
      render json: { status: @run&.status || "unknown", finished: false, error: e.class.to_s }, status: :ok
    end

    private

    def client
      EstimatorAi::IndexClient.new(timeout: EXPAND_TIMEOUT_SECONDS)
    end

    def fetch_stats
      client.corpus_stats
    end

    # Accept either a single budget object or an array of them.
    def parse_documents(text)
      parsed = JSON.parse(text)
      parsed.is_a?(Array) ? parsed : [ parsed ]
    end

    EXAMPLE_JSON = <<~JSON.freeze
      {
        "budget_id": "NEW-2025-9100",
        "client_metadata": { "name": "Nueva Cuenta S.L.", "sector": "finance", "country": "ES" },
        "project_summary": "New historical project to add to the corpus",
        "main_technology": "Python",
        "year": 2025,
        "total_estimated_hours": 200,
        "components": [
          { "component_id": "AUTH-001", "name": "OAuth2 login", "description": "Auth backend",
            "module": "Authentication & Access", "tech_stack": ["Python"], "estimated_hours": 120,
            "complexity": "medium", "dependencies": [] },
          { "component_id": "RECON-001", "name": "Payment reconciliation", "description": "Nightly batch",
            "module": "Payments & Billing", "tech_stack": ["Python"], "estimated_hours": 80,
            "complexity": "medium", "dependencies": [] }
        ]
      }
    JSON
  end
end
