# Session 13 — the GRAPH-driven estimation wizard.
#
# The whole orchestration lives in the service IA as a LangGraph multi-agent graph;
# this controller only does what a business backend must: START the run, render each
# human gate the graph pauses at, and RESUME the run with the person's decision. Three
# HTTP verbs against the service, one per human touch-point:
#
#   create        → POST /v1/estimate/graph                      (start → gate 1)
#   resume_structure → POST /v1/estimate/graph/:id/resume        (gate 1 → gate 2)
#   resume_final     → POST /v1/estimate/graph/:id/resume        (gate 2 → done)
#
# The graph may sit paused for minutes or days between these calls — its state is held
# by the service's Postgres checkpointer, mirrored here into the run row. The pattern
# is stack-agnostic: any HTTP client could drive the same resumes.
module Rag
  class GraphEstimationRunsController < ApplicationController
    # The module→task param parsing shared by both human gates.
    include GraphResumeParams

    def index
      @runs = Rag::GraphEstimationRun.order(created_at: :desc).limit(20)
    end

    def new
      @run = Rag::GraphEstimationRun.new
    end

    # START the graph: runs the classifier + structure agents and pauses at gate 1.
    def create
      transcript = params.dig(:graph_estimation_run, :transcript).to_s.strip
      estimation_id = SecureRandom.uuid
      @run = Rag::GraphEstimationRun.new(
        pricing_params.merge(transcript: transcript, estimation_id: estimation_id)
      )
      unless @run.valid?
        flash.now[:alert] = "Pega una transcripción para empezar."
        return render :new, status: :unprocessable_entity
      end
      # Persist the run BEFORE calling the service, so a guardrail rejection (or a
      # timeout) leaves a row the person can reopen and retry — same posture as S12.
      @run.save!

      guard_graph_errors do
        # Kick the graph off in the BACKGROUND (202) and go straight to the live panel;
        # the classifier + structure agents report their progress there as they run.
        graph_client.graph_start_stream(transcript: transcript, estimation_id: estimation_id)
        @run.update!(graph_state: "running")
        redirect_to rag_graph_estimation_run_path(@run),
                    notice: "Grafo iniciado. Sigue en vivo lo que hace cada agente."
      end
    end

    def show
      @run = Rag::GraphEstimationRun.find(params[:id])
    end

    # HUMAN GATE 1 → resume with the reviewed module→task breakdown (in background).
    def resume_structure
      @run = Rag::GraphEstimationRun.find(params[:id])
      decision = { "approved" => true, "modules" => reviewed_modules }
      guard_graph_errors do
        graph_client.graph_resume_stream(estimation_id: @run.estimation_id, decision: decision)
        @run.update!(graph_state: "running")
        redirect_to rag_graph_estimation_run_path(@run),
                    notice: "Estructura aprobada. Sigue en vivo las horas y el análisis."
      end
    end

    # HUMAN GATE 2 → resume with the final validation (+ optional proposal, in background).
    # The human may have completed/adjusted per-task hours; we patch the stored estimate
    # by index (the structure is fixed at gate 2) and send it as estimate_overrides. The
    # service recomputes totals/confidence from the edited hours.
    def resume_final
      @run = Rag::GraphEstimationRun.find(params[:id])
      decision = {
        "validated" => true,
        "want_proposal" => ActiveModel::Type::Boolean.new.cast(params[:want_proposal]) || false,
        "estimate_overrides" => { "modules" => estimate_modules_with_edited_hours }
      }
      # Money rides along with the human's decision: this is the first moment the final
      # hours exist, and it is the ONLY place the price crosses the wire. The service just
      # carries it so the proposal can quote it — the figure itself is computed here.
      decision["pricing"] = pricing_payload_for(estimate_modules_with_edited_hours)
      guard_graph_errors do
        graph_client.graph_resume_stream(estimation_id: @run.estimation_id, decision: decision)
        @run.update!(graph_state: "running")
        redirect_to rag_graph_estimation_run_path(@run),
                    notice: "Estimación validada. Redactando el cierre en vivo…"
      end
    end

    # Draft (or re-draft) the commercial proposal after completion — over the run's
    # validated estimate, no graph re-run. Available even if it was not asked for at gate 2.
    def generate_proposal
      @run = Rag::GraphEstimationRun.find(params[:id])
      guard_graph_errors do
        # Send the run's CURRENT pricing: the service would otherwise quote whatever was
        # frozen into the graph state back at gate 2.
        proposal = graph_client.graph_proposal(estimation_id: @run.estimation_id,
                                               pricing: @run.pricing.to_payload)
        warn_on_price_drift(proposal)
        @run.update!(proposal: proposal["body_markdown"], proposal_title: proposal["title"])
        redirect_to rag_graph_estimation_run_path(@run),
                    notice: "Propuesta redactada por el Arquitecto."
      end
    end

    # Download the proposal as a basic PDF (Prawn). 302 back if there is no proposal yet.
    def proposal_pdf
      @run = Rag::GraphEstimationRun.find(params[:id])
      unless @run.proposal?
        return redirect_to rag_graph_estimation_run_path(@run),
                           alert: "Aún no hay propuesta. Genérala primero."
      end
      send_data Rag::ProposalPdf.new(@run).render,
                type: "application/pdf",
                filename: "propuesta-#{@run.id}.pdf",
                disposition: "inline"
    end

    # The proposal DOCUMENT as Markdown: the agent's prose plus the budget tables composed
    # by Rag::BudgetBreakdown. The PDF renders the same data; this is the portable version.
    def proposal_md
      @run = Rag::GraphEstimationRun.find(params[:id])
      unless @run.proposal?
        return redirect_to rag_graph_estimation_run_path(@run),
                           alert: "Aún no hay propuesta. Genérala primero."
      end

      send_data @run.proposal_markdown,
                type: "text/markdown; charset=utf-8",
                filename: "propuesta-#{@run.id}.md",
                disposition: "attachment"
    end

    # LIVE POLL (JSON) — the graph-progress Stimulus controller hits this every ~1.5s
    # while a leg runs. Returns the per-agent activity feed; on a terminal state it
    # persists the artifacts so the reload renders the gate / completed screen.
    def progress
      @run = Rag::GraphEstimationRun.find(params[:id])
      data = graph_client.graph_progress(estimation_id: @run.estimation_id)
      finished = leg_finished?(data)
      @run.apply_run_state!(data) if finished
      render json: {
        finished: finished,
        state: finished ? data["state"] : "running",
        activity: data["activity"] || []
      }
    rescue EstimatorAi::Error, Faraday::Error
      # Transient error mid-run — keep the poller alive (mirrors index_runs#status).
      render json: { finished: false, state: "running", activity: [] }
    end

    private

    # The two knobs from the transcript screen. Blank fields fall back to the column
    # defaults rather than to zero, so a hurried start still prices.
    def pricing_params
      given = params.fetch(:graph_estimation_run, {}).permit(:rate_eur_per_hour, :contingency_pct)
      given.to_h.reject { |_k, v| v.to_s.strip.blank? }
    end

    # The price the service will quote, derived from the hours the human just confirmed
    # rather than from the stored estimate (which the resume is about to overwrite).
    def pricing_payload_for(modules)
      hours = Array(modules).sum { |m| Array(m["tasks"]).sum { |t| t["estimated_hours"].to_f } }
      Rag::Pricing.new(hours: hours,
                       rate_eur_per_hour: @run.rate_eur_per_hour,
                       contingency_pct: @run.contingency_pct).to_payload
    end

    # The agent is told to copy the total verbatim. If it comes back different it derived
    # its own figure, which is the one failure mode the prompt cannot fully prevent — so it
    # gets logged. The screens and the PDF always render OUR number regardless.
    def warn_on_price_drift(proposal)
      quoted = proposal["total_price_eur"]
      expected = @run.pricing.total.round
      return if quoted.blank? || !@run.pricing.priced? || quoted.to_i == expected

      Rails.logger.warn(
        "[proposal] price drift: the agent quoted #{quoted} EUR but was given #{expected} EUR " \
        "(run #{@run.id})"
      )
    end

    def graph_client(timeout: Rails.application.config.estimator_ai.timeout)
      EstimatorAi::RagEstimateClient.new(timeout: timeout)
    end

    # ``reviewed_modules`` (gate 1) and ``estimate_modules_with_edited_hours`` (gate 2)
    # live in the GraphResumeParams concern.

    # --- Is the leg really over? ----------------------------------------------
    #
    # Both START and RESUME return 202 and run the graph in a BackgroundTask, so for the
    # first moments the checkpoint has not moved yet. The service derives its answer from
    # that checkpoint (``estimate_graph.py::_progress_state``), which produces two distinct
    # terminal answers that are LIES:
    #
    #   1. AFTER START there is no checkpoint at all, so there is no ``snapshot.next`` and
    #      the service answers "completed" for a run that has not begun.
    #   2. AFTER A RESUME the checkpoint still holds the interrupt we just resumed FROM, so
    #      it answers "paused" at the gate the reviewer has only just approved.
    #
    # Taking either at face value ends the leg on the very first poll: (1) reloads into an
    # empty result screen seconds after pressing start, (2) bounces the reviewer straight
    # back to the gate they approved.
    #
    # Both are caught without a timer. (1) is evidence-based — a genuinely paused leg
    # carries a ``pending_gate``, a completed one an ``estimate``, and a failed one an
    # "error" line in the feed; only a not-yet-started run is empty on every count. (2)
    # compares the reported gate against ``current_gate``, which still holds whatever the
    # last ``apply_run_state!`` wrote — i.e. exactly the gate this leg resumed from (nil on
    # a START, so only the first guard applies there).
    def leg_finished?(data)
      return false if data["state"] == "running"

      started = data["activity"].present? || data["pending_gate"].present? ||
                data["estimate"].present? || data["structure"].present?
      return false unless started

      gate = data.dig("pending_gate", "gate")
      gate.blank? || gate != @run.current_gate
    end

    # Mirrors the Session 9/12 wizard's error posture (GuardrailViolation, timeouts).
    def guard_graph_errors
      yield
    rescue EstimatorAi::GuardrailViolation => e
      redirect_back_to_run("Entrada rechazada por guardarraíles: #{e.message}")
    rescue EstimatorAi::InvalidRequest => e
      redirect_back_to_run("Petición inválida: #{e.message}")
    rescue EstimatorAi::ServerError => e
      redirect_back_to_run("Error del servicio IA: #{e.message}")
    rescue Faraday::TimeoutError, Faraday::ConnectionFailed => e
      redirect_back_to_run("El servicio IA no respondió a tiempo. Los agentes gpt-5 pueden " \
                           "tardar; reintenta. (#{e.class})")
    end

    def redirect_back_to_run(message)
      flash[:alert] = message
      if @run&.persisted?
        redirect_to rag_graph_estimation_run_path(@run)
      else
        redirect_to new_rag_graph_estimation_run_path
      end
    end
  end
end
