# Session 14 — the supervisor flow and its human review inbox.
#
# Thin by design: the service IA owns the orchestration (which agent acts, whether to
# pause), and this controller only starts runs, renders whatever came back, and feeds
# the reviewer's decision to the resume endpoint.
#
# Two postures carried over from the Session 13 wizard because they earn their keep:
#   * the row is SAVED BEFORE the service is called, so a guardrail rejection still
#     leaves a reopenable record rather than losing the transcript;
#   * one ``apply_run_state!`` maps every service response, so the persisted shape
#     cannot drift from the contract.
module Rag
  class SupervisorEstimationRunsController < ApplicationController
    before_action :set_run, only: %i[show resume]

    # The inbox first: what is waiting for a person is the reason this screen exists.
    def index
      @awaiting = Rag::SupervisorEstimationRun.awaiting_review.limit(50)
      @recent = Rag::SupervisorEstimationRun.recent.limit(20)
    end

    def new
      @run = Rag::SupervisorEstimationRun.new
    end

    def create
      transcript = params.dig(:supervisor_estimation_run, :transcript).to_s.strip
      @run = Rag::SupervisorEstimationRun.new(
        transcript: transcript, estimation_id: SecureRandom.uuid
      )
      return render :new, status: :unprocessable_entity unless @run.save

      guard_supervisor_errors do
        @run.apply_run_state!(
          client.supervisor_start(transcript: transcript, estimation_id: @run.estimation_id)
        )
        redirect_to rag_supervisor_estimation_run_path(@run)
      end
    end

    def show
    end

    # The human's answer to the review gate. "adjust" carries the edited component
    # days; the service rederives the headline total from them.
    def resume
      guard_supervisor_errors do
        @run.apply_run_state!(
          client.supervisor_resume(
            estimation_id: @run.estimation_id,
            decision: decision_param,
            estimate_overrides: estimate_overrides,
            note: params[:note].presence
          )
        )
        redirect_to rag_supervisor_estimation_run_path(@run)
      end
    end

    private

    def set_run
      @run = Rag::SupervisorEstimationRun.find(params[:id])
    end

    def client
      EstimatorAi::RagEstimateClient.new(timeout: Rails.application.config.estimator_ai.timeout)
    end

    def decision_param
      decision = params[:decision].to_s
      %w[approve adjust reject].include?(decision) ? decision : "approve"
    end

    # Only meaningful for "adjust": patch the stored estimate's components BY INDEX
    # with the days the reviewer typed. The structure is read-only on this screen, so
    # index matching is safe here — it would not be if the reviewer could add or
    # reorder components.
    def estimate_overrides
      return nil unless decision_param == "adjust"

      edited = params[:components]
      return nil if edited.blank?

      components = @run.estimate_components.each_with_index.map do |component, index|
        raw = values_of(edited)[index]&.dig(:engineer_days)
        next component if raw.nil?

        component.merge("engineer_days" => raw.to_s.strip.presence&.to_i)
      end
      { "components" => components }
    end

    # Params arrive as an integer-keyed hash ({"0" => ..., "1" => ...}); sort by the
    # numeric key so the order matches the rendered rows.
    def values_of(collection)
      return collection if collection.is_a?(Array)

      collection.to_unsafe_h.sort_by { |key, _| key.to_i }.map(&:last)
    end

    def guard_supervisor_errors
      yield
    rescue EstimatorAi::GuardrailViolation => e
      redirect_back_to_run("Entrada rechazada por guardarraíles: #{e.message}")
    rescue EstimatorAi::InvalidRequest => e
      redirect_back_to_run("Petición inválida: #{e.message}")
    rescue EstimatorAi::ServerError => e
      redirect_back_to_run("Error del servicio IA: #{e.message}")
    rescue Faraday::TimeoutError, Faraday::ConnectionFailed => e
      redirect_back_to_run("El servicio IA no respondió a tiempo; reintenta. (#{e.class})")
    end

    def redirect_back_to_run(message)
      flash[:alert] = message
      if @run&.persisted?
        redirect_to rag_supervisor_estimation_run_path(@run)
      else
        redirect_to new_rag_supervisor_estimation_run_path
      end
    end
  end
end
